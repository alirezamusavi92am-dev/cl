import os
os.environ.setdefault("LOCALCHAT_OFFLINE", "1")
import json
import random
import struct
import unittest
import zlib
from datetime import datetime, timezone
from unittest.mock import patch

from local_chat import forex_analysis as fa
from local_chat import forex_chart, forex_desk, forex_intent, forex_prob, forex_service, forex_vision, mt5_data
from local_chat import server as srv
from local_chat.test_features import FeatureTestCase

NOW = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)      # Thursday, markets open
TICK = int(NOW.timestamp()) + 7200 - 3                        # broker clock is UTC+2
CONFIG = {"enabled": True, "terminal_path": "", "symbol_suffix": "", "default_timeframe": "M15",
          "use_vision": True, "vision_model": "", "text_model": ""}


def walk(n, seed, persist=0.0, step=900, sigma=0.0004):
    """Seeded random walk of M15-sized candles; ``persist`` adds momentum (a real edge)."""
    rng = random.Random(seed)
    closes, momentum = [1.1], 0.0
    for _ in range(n - 1):
        momentum = persist * momentum + rng.gauss(0, sigma)
        closes.append(closes[-1] + momentum)
    opens = [closes[0]] + closes[:-1]
    highs = [max(a, b) + abs(rng.gauss(0, sigma / 2)) for a, b in zip(opens, closes)]
    lows = [min(a, b) - abs(rng.gauss(0, sigma / 2)) for a, b in zip(opens, closes)]
    last_open = (TICK // step) * step
    return {"time": [last_open - (n - 1 - i) * step for i in range(n)],
            "open": opens, "high": highs, "low": lows, "close": closes}


def resample(rates, k, seconds):
    out = {key: [] for key in rates}
    for i in range(0, len(rates["close"]) - k + 1, k):
        out["open"].append(rates["open"][i])
        out["high"].append(max(rates["high"][i:i + k]))
        out["low"].append(min(rates["low"][i:i + k]))
        out["close"].append(rates["close"][i + k - 1])
    last_open = (TICK // seconds) * seconds
    out["time"] = [last_open - (len(out["close"]) - 1 - i) * seconds for i in range(len(out["close"]))]
    return out


def fake_fetch(persist=0.0, seed=7):
    base = walk(6000, seed, persist)

    def fetch(symbol, frames, bars=None, suffix="", terminal_path=""):
        rates = {}
        for name in frames:
            seconds = fa.TIMEFRAME_SECONDS[name]
            if seconds >= 900:
                series = resample(base, seconds // 900, seconds) if seconds > 900 else base
            else:  # frames below M15 are not used by the maths: reuse the M15 candles
                series = {k: list(v) for k, v in base.items()}
            count = (bars or {}).get(name, 500) if isinstance(bars, dict) else (bars or 500)
            rates[name] = {k: v[-count:] for k, v in series.items()}
        bid = rates[frames[0]]["close"][-1]
        return {"symbol": symbol, "broker_symbol": symbol, "info": {"digits": 5, "point": 1e-5},
                "tick": {"time": TICK, "bid": bid, "ask": bid + 2e-5}, "rates": rates, "failed_timeframes": []}
    return fetch


class TradingProfileTests(unittest.TestCase):
    def profile(self, *texts):
        return forex_intent.trading_profile([{"role": "user", "content": t} for t in texts])

    def test_timeframe_statement_is_remembered_across_messages(self):
        profile = self.profile("EURUSD رو برای ترید در تایم فریم ۱۵ دقیقه بررسی کن", "حمایت‌ها چنده؟")
        self.assertEqual((profile["timeframe"], profile["explicit"]), ("M15", True))

    def test_newest_statement_wins(self):
        self.assertEqual(self.profile("تایم فریم 15", "حالا H1 رو بگو")["timeframe"], "H1")

    def test_trade_cue_picks_the_trading_frame_among_several(self):
        self.assertEqual(self.profile("تحلیل H4 و برای ترید روی 5 دقیقه")["timeframe"], "M5")
        self.assertEqual(self.profile("gbpusd h4 m15")["timeframe"], "M15")   # no cue: the lowest

    def test_style_words_and_default(self):
        self.assertEqual(self.profile("اسکالپ طلا")["timeframe"], "M5")
        self.assertEqual(self.profile("سوینگ یورو")["timeframe"], "H4")
        none = self.profile("سلام")
        self.assertEqual((none["timeframe"], none["explicit"]), ("M15", False))

    def test_bare_number_after_timeframe_means_minutes(self):
        self.assertEqual(self.profile("تایم‌فریم ۳۰")["timeframe"], "M30")
        self.assertEqual(self.profile("تایم فریم 60")["timeframe"], "H1")

    def test_plan_has_trigger_trading_and_higher_frames(self):
        self.assertEqual(forex_intent.plan_timeframes("M15"), ["M5", "M15", "H1", "H4", "D1"])
        self.assertEqual(forex_intent.plan_timeframes("H4"), ["H1", "H4", "D1", "W1"])
        self.assertIn("M30", forex_intent.plan_timeframes("M15", extra=["M30"]))


class ProbabilityEngineTests(unittest.TestCase):
    @staticmethod
    def run_model(persist, seed=7):
        base = walk(6000, seed, persist)
        return base, forex_prob.analyze(base, "M15", {"H1": resample(base, 4, 3600), "H4": resample(base, 16, 14400)})

    def test_probabilities_are_a_distribution_with_ordered_intervals(self):
        _base, model = self.run_model(0.0)
        self.assertAlmostEqual(sum(model["probs"].values()), 1.0, places=6)
        self.assertAlmostEqual(sum(model["base"].values()), 1.0, places=6)
        for name, (low, high) in model["ci80"].items():
            self.assertLessEqual(low, model["probs"][name])
            self.assertLessEqual(model["probs"][name], high)

    def test_random_walk_has_no_demonstrable_edge_and_is_damped(self):
        _base, model = self.run_model(0.0)
        self.assertEqual(model["grade"], "بدون لبه")
        for name in ("up", "down", "range"):
            self.assertLess(abs(model["probs"][name] - model["base"][name]), 0.06)

    def test_momentum_series_is_validated_as_an_edge(self):
        _base, model = self.run_model(0.85)
        self.assertEqual(model["grade"], "قابل اتکا")
        self.assertGreater(model["validation"]["skill"], 0.02)
        self.assertEqual(model["damping"], 1.0)

    def test_deterministic(self):
        self.assertEqual(self.run_model(0.5)[1]["probs"], self.run_model(0.5)[1]["probs"])

    def test_future_candles_do_not_leak_into_the_state_of_old_bars(self):
        base = walk(900, 3, 0.4)
        features = forex_prob.build_features(base, "M15", {})
        altered = {k: list(v) for k, v in base.items()}
        altered["close"][-1] += 0.05
        altered["high"][-1] += 0.05
        again = forex_prob.build_features(altered, "M15", {})
        for key in ("trend5", "loc", "adx2", "rsi3"):
            self.assertEqual(features[key][:-1], again[key][:-1], key)

    def test_too_little_history_gives_no_probabilities(self):
        short = walk(300, 1)
        self.assertIsNone(forex_prob.analyze(short, "M15", {}))

    def test_first_passage_rules(self):
        opens, highs, lows = [1, 1, 1, 1], [1.0, 1.2, 1.0, 1.0], [1.0, 0.99, 0.7, 0.99]
        self.assertEqual(forex_prob.first_passage(opens, highs, lows, 0, 1, 1.15, 0.8, 3), 1)
        self.assertEqual(forex_prob.first_passage(opens, highs, lows, 0, 1, 1.5, 0.8, 3), -1)
        self.assertEqual(forex_prob.first_passage(opens, highs, lows, 0, 1, 1.5, 0.5, 3), 0)
        # both inside one candle: the barrier nearer to the open is taken
        self.assertEqual(forex_prob.first_passage([1.0, 1.0], [1.0, 1.3], [1.0, 0.6], 0, 1, 1.3, 0.6, 1), 1)

    def test_scenarios_have_consistent_levels_and_odds(self):
        base, model = self.run_model(0.6)
        analysis = fa.analyze_timeframe("M15", base, None, True)
        scenarios = forex_prob.build_scenarios(base, model, analysis, 0.0002)
        self.assertEqual({s["direction"] for s in scenarios}, {"long", "short"})
        for s in scenarios:
            sign = 1 if s["direction"] == "long" else -1
            self.assertGreater((s["target1"] - s["entry"]) * sign, 0)
            self.assertLess((s["stop"] - s["entry"]) * sign, 0)
            odds = s["odds"]
            self.assertAlmostEqual(odds["target"] + odds["stop"] + odds["timeout"], 1.0, places=6)
            self.assertAlmostEqual(s["expectancy_net_r"], odds["expectancy_r"] - s["cost_r"], places=9)


class ChartAndVisionTests(unittest.TestCase):
    def test_chart_is_a_valid_png_of_the_requested_size(self):
        rates = walk(300, 2, 0.5)
        closes = rates["close"]
        png = forex_chart.render(rates, fa.ema(closes, 20), fa.ema(closes, 50),
                                 supports=[closes[-1] - 0.002], resistances=[closes[-1] + 0.002],
                                 pivot=closes[-1], price=closes[-1])
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", png[16:24])
        self.assertEqual((width, height), (960, 540))
        idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8]
        self.assertEqual(len(zlib.decompress(idat)), (960 * 3 + 1) * 540)

    def test_chart_rejects_tiny_input(self):
        with self.assertRaises(ValueError):
            forex_chart.render(walk(3, 1))

    def test_vision_reply_is_parsed_defensively(self):
        reply = 'Sure!\n{"trend":"UP","structure":"HH/HL","patterns":["flag","a","b","c"],"bias":"bull","confidence":"high","x":1}'
        parsed = forex_vision.parse_reply(reply)
        self.assertEqual((parsed["trend"], parsed["bias"], parsed["confidence"]), ("up", "bull", "high"))
        self.assertEqual(len(parsed["patterns"]), 3)
        self.assertIsNone(forex_vision.parse_reply("no json here"))
        self.assertEqual(forex_vision.parse_reply('{"bias":"moon"}')["bias"], "neutral")

    def test_agreement_logic(self):
        self.assertEqual(forex_vision.agreement("up", "bull"), "agree")
        self.assertEqual(forex_vision.agreement("up", "bear"), "conflict")
        self.assertEqual(forex_vision.agreement("balanced", "bull"), "neutral")
        self.assertEqual(forex_vision.agreement("down", "neutral"), "neutral")

    def test_read_chart_never_raises(self):
        def broken(*_):
            raise OSError("down")
        self.assertFalse(forex_vision.read_chart(broken, "m", "AAAA", "EURUSD", "M15")["ok"])
        good = forex_vision.read_chart(lambda *_: '{"bias":"bear"}', "m", "AAAA", "EURUSD", "M15")
        self.assertTrue(good["ok"])


class DeskTests(unittest.TestCase):
    def analyze(self, persist=0.6, **kw):
        with patch.object(mt5_data, "fetch_symbol", fake_fetch(persist)):
            return forex_desk.analyze_symbol("EURUSD", "M15", CONFIG, now=NOW, **kw)

    def test_report_digest_and_json(self):
        report = self.analyze(with_chart=True)
        self.assertIsNotNone(report["quant"])
        digest = forex_desk.format_digest(report, 6000)
        for needle in ("معامله روی M15", "احتمال تجربی", "سناریو لانگ", "سناریو شورت", "اعتبار", "افق مدل"):
            self.assertIn(needle, digest)
        self.assertLessEqual(len(forex_desk.format_digest(report, 2200)), 2200)
        public = forex_desk.public_report(report)
        json.dumps(public)                      # must be serialisable
        self.assertTrue(public["chart"].startswith("data:image/png;base64,"))
        self.assertNotIn("_features", public["quant"])

    def test_trading_timeframe_gets_deep_history_and_higher_frames_cover_it(self):
        plan = forex_desk.bars_plan("M15", ["M5", "M15", "H1", "H4", "D1"], True)
        self.assertEqual(plan["M15"], mt5_data.DEEP_BARS)
        self.assertGreaterEqual(plan["H1"], mt5_data.DEEP_BARS // 4)
        self.assertGreaterEqual(plan["H4"], mt5_data.DEEP_BARS // 16)
        self.assertEqual(forex_desk.bars_plan("M15", ["M15"], False)["M15"], mt5_data.DEFAULT_BARS)

    def test_shallow_mode_skips_the_model(self):
        report = self.analyze(deep=False)
        self.assertIsNone(report["quant"])
        self.assertEqual(report["scenarios"], [])

    def test_chat_context_is_compact_and_remembers_the_timeframe(self):
        messages = [{"role": "user", "content": "EURUSD برای ترید در تایم فریم ۱۵ دقیقه"},
                    {"role": "assistant", "content": "x" * 5000},
                    {"role": "user", "content": "سطح حمایت و مقاومتش چیه؟"}]
        with patch.object(mt5_data, "fetch_symbol", fake_fetch(0.6)) as fetch, \
                patch.object(forex_service.app_settings, "forex_settings", return_value=CONFIG):
            result = forex_service.build_context(messages, 6500, now=NOW)
        self.assertTrue(result.ok)
        self.assertEqual(result.profile["timeframe"], "M15")
        self.assertIn("معامله روی M15", result.context)
        self.assertLessEqual(len(result.context), 6500)
        self.assertTrue(any("تایم‌فریم معامله: M15" in n for n in result.notices))

    def test_old_assistant_answers_are_shortened_but_the_last_one_is_kept(self):
        history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "A" * 3000},
                   {"role": "user", "content": "b"}, {"role": "assistant", "content": "B" * 3000},
                   {"role": "user", "content": "c"}]
        compact = forex_service.compact_history(history)
        self.assertLess(len(compact[1]["content"]), 700)
        self.assertGreater(len(compact[3]["content"]), 1300)
        self.assertEqual(compact[0], history[0])


class DeskEndpointTests(FeatureTestCase):
    def analyze(self, payload):
        status, body = self.call("POST", "/api/forex/analyze", payload)
        return status, ([json.loads(x) for x in body.decode().splitlines() if x.strip()] if status == 200 else json.loads(body))

    def test_requires_creator_and_validates_input(self):
        with srv._identity_session() as connection:
            connection.execute("DELETE FROM creator_identity")
        status, _ = self.call("POST", "/api/forex/analyze", {"symbol": "EURUSD"})
        self.assertEqual(status, 403)
        status, _ = self.call("POST", "/api/forex/explain", {"model": "text:8b"})
        self.assertEqual(status, 403)

    def test_bad_input_is_rejected(self):
        self.assertEqual(self.analyze({"symbol": "hello"})[0], 400)
        self.assertEqual(self.analyze({"symbol": "EURUSD", "timeframe": "M7"})[0], 400)
        self.assertEqual(self.analyze({"symbol": "EURUSD", "vision": "yes"})[0], 400)
        status, _ = self.call("POST", "/api/forex/explain", {"model": "../x", "analysis_id": "a"})
        self.assertEqual(status, 400)

    def test_analyze_then_explain_with_vision(self):
        read = json.dumps({"trend": "down", "structure": "LH-LL", "patterns": ["flag"], "bias": "bear",
                           "confidence": "medium", "price_vs_emas": "below both", "nearest_reaction_zone": "1.10"})
        with patch.object(mt5_data, "fetch_symbol", fake_fetch(0.85)), \
                patch.object(srv, "_forex_vision_chat", return_value=read) as vision:
            status, events = self.analyze({"symbol": "eur/usd", "timeframe": "M15", "vision": True})
        self.assertEqual(status, 200)
        kinds = [next(iter(e)) for e in events]
        self.assertIn("report", kinds)
        self.assertEqual(kinds[-1], "done")
        report = next(e["report"] for e in events if "report" in e)
        self.assertEqual((report["symbol"], report["timeframe"]), ("EURUSD", "M15"))
        self.assertAlmostEqual(sum(report["quant"]["probs"].values()), 1.0, places=6)
        verdict = next(e["vision"] for e in events if "vision" in e)
        self.assertTrue(verdict["ok"])
        self.assertIn(verdict["agreement"], ("agree", "conflict", "neutral"))
        self.assertIn(vision.call_args.args[0], ("qwen3-vl:8b", "gemma4:e4b"))   # an installed vision model was auto-picked
        self.assertTrue(vision.call_args.args[1][0]["images"][0])         # the chart was sent

        identifier = events[-1]["analysis_id"]
        status, body = self.call("POST", "/api/forex/explain", {
            "analysis_id": identifier, "model": "text:8b", "question": "اگر H1 بشکنه چی؟",
            "history": [{"role": "user", "content": "گزارش کامل"}, {"role": "assistant", "content": "y" * 4000}]})
        self.assertEqual(status, 200)
        self.assertEqual(self.reply_text(self.chunks(body)), self.ollama.chat_reply)
        sent = self.ollama.chat_requests()[-1]["messages"]
        joined = "\n".join(m["content"] for m in sent if m["role"] == "system")
        self.assertIn("معامله روی M15", joined)
        self.assertIn("خوانش تصویری", joined)                               # vision read reached the digest
        self.assertNotIn("y" * 1500, "\n".join(m["content"] for m in sent))  # long answer trimmed to ~1400

    def test_explain_unknown_analysis_is_404_with_code(self):
        status, body = self.call("POST", "/api/forex/explain", {"analysis_id": "nope", "model": "text:8b"})
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["code"], "analysis_expired")

    def test_terminal_problem_becomes_a_stream_error(self):
        error = mt5_data.MT5Error("ترمینال متصل نیست.", "not_connected")
        with patch.object(mt5_data, "fetch_symbol", side_effect=error):
            status, events = self.analyze({"symbol": "EURUSD", "vision": False})
        self.assertEqual(status, 200)
        self.assertEqual(events[-1]["code"], "not_connected")

    def test_page_and_assets_are_served(self):
        for path, kind in (("/forex", b"<html"), ("/static/forex.js", b"use strict"), ("/static/forex.css", b"--bg")):
            status, body = self.call("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(kind, body[:400] if kind == b"<html" else body)

    def test_default_timeframe_setting_round_trips_and_is_validated(self):
        self.assertEqual(self.call("POST", "/api/settings/forex", {"default_timeframe": "M7"})[0], 400)
        status, body = self.call("POST", "/api/settings/forex", {"default_timeframe": "H1", "use_vision": False})
        self.assertEqual(status, 200)
        self.assertEqual(app_settings_forex()["default_timeframe"], "H1")


def app_settings_forex():
    from local_chat import settings
    return settings.forex_settings()


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------------
# session feature, news calendar, validation tool
# ---------------------------------------------------------------------------------------
import os                                                  # noqa: E402
from local_chat import forex_news, forex_validate          # noqa: E402
from local_chat import settings as app_settings            # noqa: E402

NOW_EPOCH = NOW.timestamp()


def feed_row(minutes, title="Core CPI m/m", country="USD", impact="High", forecast="0.3%", previous="0.2%"):
    when = datetime.fromtimestamp(NOW_EPOCH + minutes * 60, timezone.utc).isoformat()
    return {"title": title, "country": country, "date": when, "impact": impact, "forecast": forecast, "previous": previous}


class SessionTests(unittest.TestCase):
    def test_hour_buckets(self):
        self.assertEqual([forex_prob.session_of(h) for h in (3, 7, 11, 12, 15, 16, 20, 21, 23)],
                         [0, 1, 1, 2, 2, 3, 3, 0, 0])

    def test_series_uses_bar_close_in_utc_and_is_off_when_meaningless(self):
        # broker clock UTC+2: a bar opening 14:00 server time closes 14:15 server = 12:15 UTC → overlap
        opening = 14 * 3600
        self.assertEqual(forex_prob.session_series([opening], 900, 7200), [2])
        self.assertEqual(forex_prob.session_series([opening], 900, None), [0])
        self.assertEqual(forex_prob.session_series([opening], 4 * 3600, 7200), [0])   # H4 spans sessions

    def test_model_reports_the_session_when_the_offset_is_known(self):
        base = walk(6000, 7, 0.5)
        htf = {"H1": resample(base, 4, 3600), "H4": resample(base, 16, 14400)}
        with_offset = forex_prob.analyze(base, "M15", htf, utc_offset=7200)
        without = forex_prob.analyze(base, "M15", htf)
        self.assertIn(with_offset["state"]["session"], forex_prob.SESSION_LABELS.values())
        self.assertEqual(without["state"]["session"], "نامشخص")


class NewsTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)          # the suite runs offline; these tests inject their own opener
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("LOCALCHAT_OFFLINE", None)
        forex_news.clear_cache()
        self.addCleanup(forex_news.clear_cache)

    def test_parse_skips_bad_rows_and_normalises(self):
        events = forex_news.parse_events([feed_row(30), {"title": "x"}, "junk", feed_row(10, impact="Low", title="y")])
        self.assertEqual([e["title"] for e in events], ["y", "Core CPI m/m"])
        with self.assertRaises(forex_news.NewsError):
            forex_news.parse_events({"not": "a list"})

    def test_currency_filter_and_gold_maps_to_usd(self):
        self.assertEqual(forex_news.currencies_of("EURUSD"), ["EUR", "USD"])
        self.assertEqual(forex_news.currencies_of("XAUUSD"), ["USD"])
        events = forex_news.parse_events([feed_row(60, country="JPY"), feed_row(90, country="USD")])
        result = forex_news.evaluate(events, "EURUSD", NOW_EPOCH, 4 * 3600)
        self.assertEqual([e["currency"] for e in result["events"]], ["USD"])

    def test_imminent_and_horizon_damping(self):
        events = forex_news.parse_events([feed_row(20)])
        result = forex_news.evaluate(events, "EURUSD", NOW_EPOCH, 4 * 3600)
        self.assertIsNotNone(result["imminent"])
        self.assertEqual(result["damping"], forex_news.HORIZON_DAMPING)
        later = forex_news.evaluate(forex_news.parse_events([feed_row(180)]), "EURUSD", NOW_EPOCH, 4 * 3600)
        self.assertIsNone(later["imminent"])
        self.assertEqual(len(later["in_horizon"]), 1)
        outside = forex_news.evaluate(forex_news.parse_events([feed_row(600)]), "EURUSD", NOW_EPOCH, 4 * 3600)
        self.assertEqual((outside["damping"], len(outside["events"])), (1.0, 1))      # listed, not in horizon
        past = forex_news.evaluate(forex_news.parse_events([feed_row(-120)]), "EURUSD", NOW_EPOCH, 4 * 3600)
        self.assertEqual(past["events"], [])

    def test_medium_events_only_matter_when_near(self):
        events = forex_news.parse_events([feed_row(400, impact="Medium"), feed_row(60, impact="Medium")])
        result = forex_news.evaluate(events, "EURUSD", NOW_EPOCH, 3600)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["damping"], 1.0)

    def test_cache_failure_is_soft_and_stale_copy_is_kept(self):
        calls = []

        class Reply:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n=-1): return self.body

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) > 1:
                raise OSError("down")
            return Reply(json.dumps([feed_row(40)]).encode())

        url = ("https://example.test/feed.json",)
        first = forex_news.assess("EURUSD", NOW_EPOCH, 3600, urls=url, opener=opener)
        self.assertTrue(first["ok"])
        forex_news.assess("EURUSD", NOW_EPOCH + 60, 3600, urls=url, opener=opener)
        self.assertEqual(len(calls), 1)                                         # cached
        later = forex_news.assess("EURUSD", NOW_EPOCH + 2 * 3600, 3600, urls=url, opener=opener)
        self.assertEqual(len(calls), 2)
        self.assertTrue(later["ok"])                                            # stale copy still served

    def test_unreachable_feed_gives_error_dict_never_raises(self):
        def opener(request, timeout):
            raise OSError("no route")
        result = forex_news.assess("EURUSD", NOW_EPOCH, 3600, urls=("https://example.test/x.json",), opener=opener)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])

    def test_offline_env_skips_network(self):
        with patch.dict(os.environ, {"LOCALCHAT_OFFLINE": "1"}):
            self.assertIsNone(forex_news.assess("EURUSD", NOW_EPOCH, 3600, opener=lambda *a, **k: 1 / 0))

    def test_settings_validate_news_options(self):
        for bad in ({"news_enabled": "yes"}, {"news_url": "http://x.com/a"}, {"news_url": "ftp://x"}, {"news_url": 5}):
            with self.assertRaises(ValueError, msg=str(bad)):
                app_settings.set_forex_settings(bad)


class DeskNewsTests(unittest.TestCase):
    def run_desk(self, news_result):
        config = {**CONFIG, "news_enabled": True}
        with patch.object(mt5_data, "fetch_symbol", fake_fetch(0.85)), \
                patch.object(forex_news, "assess", return_value=news_result):
            return forex_desk.analyze_symbol("EURUSD", "M15", config, now=NOW)

    def test_imminent_news_warns_and_damps_probabilities(self):
        calm = self.run_desk(None)
        events = forex_news.parse_events([feed_row(20)])
        news = forex_news.evaluate(events, "EURUSD", NOW_EPOCH, 4 * 3600)
        busy = self.run_desk(news)
        self.assertTrue(any("خبر مهم" in w for w in busy["warnings"]))
        self.assertEqual(busy["quant"]["news_damping"], forex_news.HORIZON_DAMPING)
        for name in ("up", "down", "range"):
            self.assertLessEqual(abs(busy["quant"]["probs"][name] - busy["quant"]["base"][name]),
                                 abs(calm["quant"]["probs"][name] - calm["quant"]["base"][name]) + 1e-9)
        digest = forex_desk.format_digest(busy, 7000)
        self.assertIn("تقویم خبری EUR/USD", digest)
        self.assertIn("Core CPI", digest)
        json.dumps(forex_desk.public_report(busy))

    def test_unavailable_calendar_is_stated_not_guessed(self):
        report = self.run_desk({"ok": False, "error": "ارتباط برقرار نشد."})
        digest = forex_desk.format_digest(report, 7000)
        self.assertIn("تقویم خبری: در دسترس نیست", digest)
        self.assertEqual(report["quant"]["news_damping"], 1.0)


class ValidateToolTests(unittest.TestCase):
    def test_rows_for_every_pair_and_timeframe_and_errors_are_kept(self):
        def analyze(symbol, tf, config, now=None, deep=True):
            self.assertFalse(config["news_enabled"])
            if symbol == "GBPUSD":
                raise mt5_data.MT5Error("نماد پیدا نشد.", "symbol")
            with patch.object(mt5_data, "fetch_symbol", fake_fetch(0.85)):
                return forex_desk.analyze_symbol(symbol, tf, config, now=NOW, deep=True)
        rows = forex_validate.validate(["EURUSD", "GBPUSD"], ["M15"], {**CONFIG, "news_enabled": True}, analyze=analyze)
        self.assertEqual([(r["symbol"], "error" in r) for r in rows], [("EURUSD", False), ("GBPUSD", True)])
        self.assertIn(rows[0]["grade"], ("قابل اتکا", "ضعیف"))
        self.assertGreater(rows[0]["skill_pct"], 0)
        self.assertIsNotNone(rows[0]["ece_pct"])
        text = forex_validate.render(rows)
        self.assertIn("EURUSD", text)
        self.assertIn("نماد پیدا نشد.", text)
