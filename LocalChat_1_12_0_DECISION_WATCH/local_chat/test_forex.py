import os
os.environ.setdefault("LOCALCHAT_OFFLINE", "1")
import fnmatch
import math
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from local_chat import forex_analysis as fa
from local_chat import forex_intent as fi
from local_chat import forex_service, mt5_data
from local_chat import settings as app_settings

NOW = datetime(2026, 11, 5, 14, 30, 0, tzinfo=timezone.utc)      # Thursday, market open
SATURDAY = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)  # market closed


def series(n, base=1.10, drift=0.0004):
    """Deterministic OHLC series (no randomness, same on every machine)."""
    c = [base + 0.02 * math.sin(i / 9) + 0.01 * math.sin(i / 3.7) + drift * i for i in range(n)]
    o = [c[0]] + c[:-1]
    h = [max(a, b) + 0.0006 + 0.0003 * abs(math.sin(i)) for i, (a, b) in enumerate(zip(o, c))]
    l = [min(a, b) - 0.0006 - 0.0003 * abs(math.cos(i)) for i, (a, b) in enumerate(zip(o, c))]
    return o, h, l, c


def make_raw(symbol="EURUSD", timeframes=("M15", "H1", "H4", "D1"), now_utc=NOW, offset=7200,
             tick_age=3, n=300, digits=5, point=1e-5, broker_symbol=None):
    """Raw data shaped like mt5_data.fetch_symbol() returns it."""
    server_now = int(now_utc.timestamp()) + offset
    tick_time = server_now - tick_age
    rates = {}
    for name in timeframes:
        seconds = fa.TIMEFRAME_SECONDS[name]
        last_open = (tick_time // seconds) * seconds
        o, h, l, c = series(n, drift=0.0002)
        rates[name] = {
            "time": [last_open - (n - 1 - i) * seconds for i in range(n)],
            "open": o, "high": h, "low": l, "close": c,
        }
    bid = rates[timeframes[0]]["close"][-1]
    spread = 2 * point
    return {
        "symbol": symbol, "broker_symbol": broker_symbol or symbol,
        "info": {"digits": digits, "point": point},
        "tick": {"time": tick_time, "bid": bid, "ask": bid + spread},
        "rates": rates, "failed_timeframes": [],
    }


class IndicatorTests(unittest.TestCase):
    """Reference values come from TA-Lib 0.8 on the same deterministic series."""

    @classmethod
    def setUpClass(cls):
        cls.o, cls.h, cls.l, cls.c = series(400, drift=0.0004)

    def assertClose(self, value, expected, tolerance=1e-9):
        self.assertIsNotNone(value)
        self.assertAlmostEqual(value, expected, delta=tolerance)

    def test_moving_averages_rsi_atr_bollinger_stochastic_match_reference(self):
        c, h, l = self.c, self.h, self.l
        self.assertClose(fa.last(fa.sma(c, 20)), 1.2437703149255248)
        self.assertClose(fa.last(fa.ema(c, 50)), 1.2444625635389848)
        self.assertClose(fa.last(fa.rsi(c, 14)), 84.96263622835522)
        self.assertClose(fa.last(fa.atr(h, l, c, 14)), 0.0045553360774695075)
        _mid, upper, lower = fa.bollinger(c, 20, 2.0)
        self.assertClose(upper[-1], 1.2717605993032606)
        self.assertClose(lower[-1], 1.215780030547789)
        k, d = fa.stochastic(h, l, c)
        self.assertClose(k[-1], 98.1176720373226)
        self.assertClose(d[-1], 97.84572587850236)

    def test_macd_and_adx_converge_to_reference(self):
        line, signal, hist = fa.macd(self.c)
        self.assertClose(line[-1], 0.006928276631213981, 1e-9)
        self.assertClose(signal[-1], 0.0023200500233911286, 1e-9)
        self.assertClose(hist[-1], 0.004608226607822853, 1e-9)
        adx, plus_di, minus_di = fa.adx(self.h, self.l, self.c)
        self.assertClose(adx[-1], 40.41649078810621, 1e-6)
        self.assertClose(plus_di[-1], 54.97480231194706, 1e-6)
        self.assertClose(minus_di[-1], 9.736387649986938, 1e-6)

    def test_lists_keep_length_and_start_with_none(self):
        c = self.c
        for values, first in ((fa.sma(c, 20), 19), (fa.ema(c, 20), 19), (fa.rsi(c, 14), 14)):
            self.assertEqual(len(values), len(c))
            self.assertTrue(all(v is None for v in values[:first]))
            self.assertIsNotNone(values[first])

    def test_short_input_gives_only_none(self):
        short = [1.0, 1.1, 1.2]
        self.assertEqual(fa.sma(short, 5), [None] * 3)
        self.assertEqual(fa.ema(short, 5), [None] * 3)
        self.assertEqual(fa.rsi(short, 14), [None] * 3)
        self.assertEqual(fa.atr(short, short, short, 14), [None] * 3)
        self.assertEqual(fa.adx(short, short, short)[0], [None] * 3)
        self.assertIsNone(fa.last([None, None]))

    def test_rsi_extremes(self):
        rising = [float(i) for i in range(1, 40)]
        self.assertEqual(fa.last(fa.rsi(rising)), 100.0)
        self.assertEqual(fa.last(fa.rsi([5.0] * 40)), 50.0)
        self.assertAlmostEqual(fa.last(fa.rsi(list(reversed(rising)))), 0.0)

    def test_pivots_and_levels(self):
        pivots = fa.classic_pivots(1.12, 1.08, 1.10)
        self.assertAlmostEqual(pivots["P"], 1.10)
        self.assertAlmostEqual(pivots["R1"], 1.12)
        self.assertAlmostEqual(pivots["S1"], 1.08)
        self.assertAlmostEqual(pivots["R2"], 1.14)
        self.assertAlmostEqual(pivots["S2"], 1.06)
        levels = fa.cluster_levels([(1, 1.1000), (5, 1.1002), (9, 1.1050)], 0.001)
        self.assertEqual([lv["touches"] for lv in levels], [2, 1])
        self.assertAlmostEqual(levels[0]["price"], 1.1001)

    def test_swing_points_and_support_resistance(self):
        highs = [1.0, 1.1, 1.2, 1.5, 1.2, 1.1, 1.0, 1.1, 1.2, 1.5, 1.2, 1.1, 1.0, 1.05, 1.1]
        lows = [h - 0.1 for h in highs]
        swing_highs, swing_lows = fa.swing_points(highs, lows)
        self.assertEqual([i for i, _p in swing_highs], [3, 9])
        self.assertTrue(swing_lows)
        supports, resistances = fa.support_resistance(highs, lows, price=1.2, atr_value=0.05)
        self.assertTrue(all(lv["price"] < 1.2 for lv in supports))
        self.assertEqual(round(resistances[0]["price"], 2), 1.5)
        self.assertEqual(resistances[0]["touches"], 2)

    def test_candle_patterns(self):
        self.assertEqual(fa.candle_pattern([1.10, 1.088], [1.101, 1.103], [1.088, 1.087], [1.09, 1.102]),
                         "bullish engulfing")
        self.assertEqual(fa.candle_pattern([1.09, 1.102], [1.101, 1.103], [1.088, 1.087], [1.10, 1.088]),
                         "bearish engulfing")
        self.assertEqual(fa.candle_pattern([1.095, 1.1000], [1.101, 1.1050], [1.094, 1.0950], [1.099, 1.1001]),
                         "doji")
        self.assertEqual(fa.candle_pattern([1.0990, 1.1000], [1.1000, 1.1012], [1.0985, 1.0950], [1.0995, 1.1010]),
                         "hammer (pin bar)")
        self.assertEqual(fa.candle_pattern([1.0990, 1.1010], [1.1000, 1.1062], [1.0985, 1.0998], [1.0995, 1.1000]),
                         "shooting star (pin bar)")
        self.assertEqual(fa.candle_pattern([1.0], [1.0], [1.0], [1.0]), "")

    def test_trend_classification(self):
        up = fa.classify_trend(1.12, 1.11, 1.10, 1.08, 0.001, 65, 0.01, 30)
        self.assertEqual(up, ("صعودی قوی", 6, 6))
        down = fa.classify_trend(1.0, 1.01, 1.02, 1.04, -0.001, 30, -0.01, 30)
        self.assertEqual(down, ("نزولی قوی", -6, 6))
        weak_up = fa.classify_trend(1.12, 1.11, 1.10, 1.08, 0.001, 65, 0.01, 15)
        self.assertEqual(weak_up[0], "صعودی")
        mixed = fa.classify_trend(1.12, 1.09, 1.10, 1.08, -0.001, 50, 0.01, 15)
        self.assertEqual(mixed[0], "رنج / بدون جهت مشخص")
        self.assertEqual(fa.classify_trend(1.0, None, None, None, None, None, None, None), ("نامشخص", 0, 0))


class MarketClockTests(unittest.TestCase):
    def utc(self, *args):
        return datetime(*args, tzinfo=timezone.utc)

    def test_us_dst_boundaries_2026(self):
        self.assertFalse(fa.new_york_is_dst(self.utc(2026, 3, 8, 6, 59)))
        self.assertTrue(fa.new_york_is_dst(self.utc(2026, 3, 8, 7, 0)))
        self.assertTrue(fa.new_york_is_dst(self.utc(2026, 11, 1, 5, 59)))
        self.assertFalse(fa.new_york_is_dst(self.utc(2026, 11, 1, 6, 0)))

    def test_weekend_closing_in_winter_time(self):
        self.assertFalse(fa.is_weekend_closed(self.utc(2026, 11, 6, 21, 59)))
        self.assertTrue(fa.is_weekend_closed(self.utc(2026, 11, 6, 22, 0)))
        self.assertTrue(fa.is_weekend_closed(self.utc(2026, 11, 7, 12, 0)))
        self.assertTrue(fa.is_weekend_closed(self.utc(2026, 11, 8, 21, 59)))
        self.assertFalse(fa.is_weekend_closed(self.utc(2026, 11, 8, 22, 0)))
        self.assertFalse(fa.is_weekend_closed(self.utc(2026, 11, 5, 14, 30)))

    def test_weekend_closing_in_summer_time(self):
        self.assertFalse(fa.is_weekend_closed(self.utc(2026, 7, 10, 20, 59)))
        self.assertTrue(fa.is_weekend_closed(self.utc(2026, 7, 10, 21, 0)))
        self.assertTrue(fa.is_weekend_closed(self.utc(2026, 7, 12, 20, 59)))
        self.assertFalse(fa.is_weekend_closed(self.utc(2026, 7, 12, 21, 0)))

    def test_sessions(self):
        self.assertEqual(fa.active_sessions(self.utc(2026, 11, 5, 3, 0)), ["Tokyo"])
        self.assertEqual(fa.active_sessions(self.utc(2026, 11, 5, 14, 30)), ["London", "New York"])
        self.assertEqual(fa.active_sessions(self.utc(2026, 11, 5, 23, 0)), [])

    def test_server_offset_needs_a_fresh_tick(self):
        now = 1_800_000_000
        self.assertEqual(fa.estimate_server_offset(now + 7200 - 3, now), 7200)
        self.assertEqual(fa.estimate_server_offset(now + 10800 - 100, now), 10800)
        self.assertEqual(fa.estimate_server_offset(now - 5, now), 0)
        self.assertIsNone(fa.estimate_server_offset(now + 7200 - 1800, now))   # stale tick: no safe answer


class SnapshotTests(unittest.TestCase):
    def test_fresh_snapshot_drops_the_forming_candle(self):
        snapshot = fa.build_snapshot(make_raw(), NOW)
        self.assertEqual(snapshot["market"], "open")
        self.assertEqual(snapshot["server_offset"], 7200)
        self.assertAlmostEqual(snapshot["tick_age"], 3.0, delta=0.5)
        self.assertEqual(set(snapshot["timeframes"]), {"M15", "H1", "H4", "D1"})
        h1 = snapshot["timeframes"]["H1"]
        self.assertTrue(h1["forming"])
        self.assertEqual(h1["bars_used"], 299)
        self.assertIsNotNone(h1["ema200"])
        self.assertEqual(snapshot["sessions"], ["London", "New York"])
        self.assertTrue(snapshot["today"]["is_today"])
        self.assertIsNotNone(snapshot["pivots"])
        self.assertAlmostEqual(snapshot["spread"], 2e-5)

    def test_weekend_keeps_every_candle_and_says_closed(self):
        raw = make_raw(now_utc=SATURDAY, tick_age=40 * 3600)
        snapshot = fa.build_snapshot(raw, SATURDAY)
        self.assertEqual(snapshot["market"], "closed")
        self.assertFalse(snapshot["timeframes"]["H1"]["forming"])
        self.assertEqual(snapshot["timeframes"]["H1"]["bars_used"], 300)
        self.assertFalse(snapshot["today"]["is_today"])

    def test_old_or_unverifiable_ticks_are_reported(self):
        stale = fa.build_snapshot(make_raw(tick_age=400), NOW)
        self.assertEqual(stale["market"], "stale")
        unknown = fa.build_snapshot(make_raw(tick_age=1800), NOW)
        self.assertEqual(unknown["market"], "unknown")
        self.assertIsNone(unknown["tick_age"])
        self.assertIn("قابل تأیید نبود", fa.format_symbol(unknown))

    def test_too_few_candles_leave_the_timeframe_out(self):
        raw = make_raw(timeframes=("H1", "D1"), n=300)
        raw["rates"]["D1"] = {key: values[-10:] for key, values in raw["rates"]["D1"].items()}
        snapshot = fa.build_snapshot(raw, NOW)
        self.assertEqual(set(snapshot["timeframes"]), {"H1"})
        self.assertEqual(snapshot["missing_timeframes"], ["D1"])
        self.assertIn("بدون داده‌ی کافی", fa.format_symbol(snapshot))

    def test_text_for_the_model_contains_the_numbers(self):
        snapshot = fa.build_snapshot(make_raw(), NOW)
        text = fa.format_symbol(snapshot)
        for expected in ("== EURUSD", "[M15", "[H4", "[D1", "RSI", "MACD", "ATR", "ADX", "پیوت کلاسیک", "هم‌جهتی", "pip"):
            self.assertIn(expected, text)
        last_close = snapshot["timeframes"]["H1"]["close"]
        self.assertIn(f"{last_close:.5f}", text)

    def test_context_always_fits_the_requested_size(self):
        snapshots = [fa.build_snapshot(make_raw(symbol=s), NOW) for s in ("EURUSD", "GBPUSD", "USDJPY")]
        full = fa.format_context(snapshots, 100000)
        self.assertGreater(len(full), 3000)
        for limit in (6000, 3500, 2500, 1500):
            text = fa.format_context(snapshots, limit)
            self.assertLessEqual(len(text), limit)
            self.assertTrue(text.startswith(fa.HEADER_TEXT[:20]))
        # shrinking removes detail but the price line of every symbol survives for limits that allow it
        self.assertIn("== GBPUSD", fa.format_context(snapshots, 6000))

    def test_metals_use_price_units_instead_of_pips(self):
        raw = make_raw(symbol="XAUUSD", digits=2, point=0.01)
        snapshot = fa.build_snapshot(raw, NOW)
        self.assertIsNone(snapshot["pip"])
        self.assertNotIn("pip", fa.format_symbol(snapshot))
        self.assertEqual(fa.pip_size("USDJPY", 3, 0.001), 0.01)
        self.assertEqual(fa.pip_size("EURUSD", 5, 0.00001), 0.0001)
        self.assertEqual(fa.pip_size("EURUSD", 4, 0.0001), 0.0001)


class IntentTests(unittest.TestCase):
    def symbols(self, text):
        return [item["symbol"] for item in fi.detect_symbols(text)]

    def test_codes_and_separators(self):
        self.assertEqual(self.symbols("تحلیل EURUSD رو بده"), ["EURUSD"])
        self.assertEqual(self.symbols("eur/usd چطوره"), ["EURUSD"])
        self.assertEqual(self.symbols("GBP-JPY"), ["GBPJPY"])
        self.assertEqual(self.symbols("gbp usd"), ["GBPUSD"])
        self.assertEqual(self.symbols("EUR vs USD"), ["EURUSD"])
        self.assertEqual(self.symbols("AUDNZD 4h"), ["AUDNZD"])

    def test_reversed_pairs_use_the_market_convention(self):
        found = fi.detect_symbols("USDEUR")
        self.assertEqual(found, [{"symbol": "EURUSD", "reversed": True}])
        self.assertEqual(self.symbols("JPYUSD"), ["USDJPY"])

    def test_gold_and_silver_only_against_the_dollar(self):
        self.assertEqual(self.symbols("xauusd"), ["XAUUSD"])
        self.assertEqual(self.symbols("XAU/USD"), ["XAUUSD"])
        self.assertEqual(self.symbols("اونس طلا رو تحلیل کن"), ["XAUUSD"])
        self.assertEqual(self.symbols("XAGUSD"), ["XAGUSD"])
        self.assertEqual(self.symbols("XAUEUR"), [])
        self.assertEqual(self.symbols("قیمت طلا"), [])

    def test_persian_names(self):
        self.assertEqual(self.symbols("یورو دلار الان کجاست"), ["EURUSD"])
        self.assertEqual(self.symbols("یورودلار"), ["EURUSD"])
        self.assertEqual(self.symbols("دلار به ین"), ["USDJPY"])
        self.assertEqual(self.symbols("پوند/ین"), ["GBPJPY"])
        self.assertEqual(self.symbols("این پوند ین چطوره"), ["GBPJPY"])
        self.assertEqual(fi.detect_symbols("دلار به یورو"), [{"symbol": "EURUSD", "reversed": True}])

    def test_local_market_phrases_are_not_pairs(self):
        for text in ("قیمت دلار امروز", "قیمت دلار و یورو", "دلار یورو", "دلار به تومان", "usd to jpy",
                     "EURUSDT", "I will try later", "latest news", "سلام"):
            self.assertEqual(self.symbols(text), [], text)

    def test_the_word_try_is_not_a_currency_next_to_a_code(self):
        self.assertEqual(self.symbols("I will try USD/JPY today"), ["USDJPY"])
        self.assertEqual(self.symbols("try usd"), [])
        self.assertEqual(self.symbols("USD TRY"), ["USDTRY"])

    def test_limit_and_duplicates(self):
        self.assertEqual(self.symbols("xauusd و EURUSD و usdjpy و gbpjpy"), ["XAUUSD", "EURUSD", "USDJPY"])
        self.assertEqual(self.symbols("EURUSD eur/usd یورو دلار"), ["EURUSD"])

    def test_timeframes(self):
        self.assertEqual(fi.detect_timeframes("تحلیل H4 و ۱۵ دقیقه"), ["M15", "H4"])
        self.assertEqual(fi.detect_timeframes("EURUSD 4h daily"), ["H4", "D1"])
        self.assertEqual(fi.detect_timeframes("نمودار ۴ ساعته و هفتگی"), ["H4", "W1"])
        self.assertEqual(fi.detect_timeframes("1m 5m 30m"), ["M1", "M5", "M30"])
        self.assertEqual(fi.detect_timeframes("سلام"), [])
        self.assertEqual(fi.merge_timeframes([]), ["M15", "H1", "H4", "D1"])
        self.assertEqual(fi.merge_timeframes(["M5"]), ["M5", "M15", "H1", "H4", "D1"])
        self.assertLessEqual(len(fi.merge_timeframes(["M1", "M5", "M30", "W1"])), 6)
        self.assertIn("D1", fi.merge_timeframes(["M1", "M5", "M30", "W1"]))

    def test_follow_up_without_a_pair_reuses_the_previous_pair_only_with_a_trading_cue(self):
        history = [
            {"role": "user", "content": "EURUSD رو تحلیل کن"},
            {"role": "assistant", "content": "..."},
        ]
        follow = history + [{"role": "user", "content": "سطح حمایتش کجاست؟"}]
        self.assertEqual(fi.symbols_for_messages(follow), ([{"symbol": "EURUSD", "reversed": False}], True))
        english = history + [{"role": "user", "content": "should I buy now?"}]
        self.assertTrue(fi.symbols_for_messages(english)[1])
        unrelated = history + [{"role": "user", "content": "یه شعر بگو"}]
        self.assertEqual(fi.symbols_for_messages(unrelated), ([], False))
        other = history + [{"role": "user", "content": "give me a short summary of Python"}]
        self.assertEqual(fi.symbols_for_messages(other), ([], False))
        new_pair = history + [{"role": "user", "content": "GBPUSD چی؟"}]
        self.assertEqual(fi.symbols_for_messages(new_pair)[0][0]["symbol"], "GBPUSD")
        self.assertEqual(fi.symbols_for_messages([]), ([], False))


# ---------------------------------------------------------------------------------------
# a fake MetaTrader5 module that behaves like the real one
# ---------------------------------------------------------------------------------------

class FakeRates:
    """Mimics the numpy structured array: rates["close"] gives a column."""

    def __init__(self, columns):
        self.columns = columns

    def __len__(self):
        return len(self.columns["time"])

    def __getitem__(self, key):
        return self.columns[key]


class FakeMT5:
    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1, TIMEFRAME_W1 = 16385, 16388, 16408, 32769

    def __init__(self, symbols=("EURUSD",), init_ok=True, connected=True, tick_age=2, rates_ok=True,
                 bars=300, visible=True, bid=1.1, ask=1.10002):
        self.symbols = list(symbols)
        self.init_ok = init_ok
        self.connected = connected
        self.rates_ok = rates_ok
        self.bars = bars
        self.visible = visible
        self.bid, self.ask = bid, ask
        self.tick_age = tick_age
        self.init_calls = []
        self.shutdown_calls = 0
        self.selected = []
        self.rate_requests = []
        self.dead_timeframes = set()

    def initialize(self, *args, **kwargs):
        self.init_calls.append((args, kwargs))
        return self.init_ok

    def shutdown(self):
        self.shutdown_calls += 1

    def last_error(self):
        return (-10003, "IPC initialize failed")

    def terminal_info(self):
        return SimpleNamespace(connected=self.connected, name="MetaTrader 5", build=5430, ping_last=24500)

    def account_info(self):
        return SimpleNamespace(server="Demo-Server", trade_mode=0, login=123456, balance=1000.0, name="Secret Name")

    def symbol_info(self, name):
        if name not in self.symbols:
            return None
        return SimpleNamespace(name=name, digits=5, point=1e-5, visible=self.visible)

    def symbols_get(self, group):
        found = tuple(SimpleNamespace(name=s) for s in self.symbols if fnmatch.fnmatch(s, group))
        return found or None

    def symbol_select(self, name, enable):
        self.selected.append(name)
        return True

    def symbol_info_tick(self, name):
        if name not in self.symbols:
            return None
        return SimpleNamespace(time=1_800_000_000 - self.tick_age, bid=self.bid, ask=self.ask)

    def copy_rates_from_pos(self, name, timeframe, start, count):
        self.rate_requests.append((name, timeframe, start, count))
        if not self.rates_ok or timeframe in self.dead_timeframes:
            return None
        o, h, l, c = series(self.bars)
        return FakeRates({"time": [1_700_000_000 + i * 900 for i in range(self.bars)],
                          "open": o, "high": h, "low": l, "close": c, "tick_volume": [1] * self.bars})


class MT5DataTests(unittest.TestCase):
    def setUp(self):
        mt5_data._STATE.update(ready=False, path="", found="")
        mt5_data._CANDIDATES_CACHE["paths"] = []
        self.fake = FakeMT5()
        patches = [
            patch.object(mt5_data, "_import_mt5", lambda: self.fake),
            patch.object(mt5_data, "TICK_WAIT_SECONDS", 0.05),
            patch.object(mt5_data, "CONNECT_WAIT_SECONDS", 0.1),
            patch.object(mt5_data.time, "sleep", lambda _seconds: None),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_fetch_returns_clean_lists_and_reuses_the_connection(self):
        raw = mt5_data.fetch_symbol("EURUSD", ["M15", "H1"])
        self.assertEqual(raw["symbol"], "EURUSD")
        self.assertEqual(raw["broker_symbol"], "EURUSD")
        self.assertEqual(set(raw["rates"]), {"M15", "H1"})
        columns = raw["rates"]["M15"]
        self.assertEqual({len(v) for v in columns.values()}, {300})
        self.assertIsInstance(columns["time"][0], int)
        self.assertIsInstance(columns["close"][0], float)
        self.assertEqual(raw["info"]["digits"], 5)
        self.assertEqual(raw["tick"]["bid"], 1.1)
        mt5_data.fetch_symbol("EURUSD", ["M15"])
        self.assertEqual(len(self.fake.init_calls), 1)
        self.assertTrue(all(request[2] == 0 for request in self.fake.rate_requests))

    def test_terminal_path_is_passed_to_initialize(self):
        mt5_data.fetch_symbol("EURUSD", ["H1"], terminal_path="C:\\MT5\\terminal64.exe")
        args, kwargs = self.fake.init_calls[0]
        self.assertEqual(args, ("C:\\MT5\\terminal64.exe",))
        self.assertIn("timeout", kwargs)

    def test_corrupt_candles_are_dropped(self):
        class Dirty(FakeMT5):
            def copy_rates_from_pos(self, *args):
                good = FakeMT5.copy_rates_from_pos(self, *args)
                cols = {key: list(values) for key, values in good.columns.items()}
                cols["close"][10] = float("nan")
                cols["high"][11], cols["low"][11] = 1.0, 2.0          # high < low
                cols["open"][12] = 0.0
                cols["time"][15] = cols["time"][14]                   # duplicate time
                return FakeRates(cols)

        self.fake = Dirty()
        raw = mt5_data.fetch_symbol("EURUSD", ["H1"])
        self.assertEqual(len(raw["rates"]["H1"]["time"]), 296)

    def test_symbol_resolution_with_suffix_and_search(self):
        self.fake = FakeMT5(symbols=["EURUSD.a", "EURUSDmicro", "XEURUSD", "GBPUSD.a"])
        self.assertEqual(mt5_data.resolve_symbol(self.fake, "EURUSD", ".a"), "EURUSD.a")
        self.assertEqual(mt5_data.resolve_symbol(self.fake, "EURUSD"), "EURUSD.a")
        self.assertEqual(mt5_data.resolve_symbol(self.fake, "GBPUSD"), "GBPUSD.a")
        with self.assertRaises(mt5_data.MT5Error) as caught:
            mt5_data.resolve_symbol(self.fake, "USDJPY")
        self.assertEqual(caught.exception.code, "symbol_not_found")
        raw = mt5_data.fetch_symbol("EURUSD", ["H1"])
        self.assertEqual(raw["broker_symbol"], "EURUSD.a")
        self.assertEqual(raw["symbol"], "EURUSD")

    def test_hidden_symbols_are_added_to_market_watch(self):
        self.fake = FakeMT5(visible=False)
        mt5_data.fetch_symbol("EURUSD", ["H1"])
        self.assertEqual(self.fake.selected, ["EURUSD"])

    def test_ipc_timeout_is_retried_then_other_terminals_are_tried(self):
        class Flaky(FakeMT5):
            def initialize(self, *args, **kwargs):
                self.init_calls.append((args, kwargs))
                return bool(args) and args[0].endswith("good/terminal64.exe")

            def last_error(self):
                return (-10005, "IPC timeout")

        self.fake = Flaky()
        mt5_data._CANDIDATES_CACHE["paths"] = ["C:/bad/terminal64.exe", "C:/good/terminal64.exe"]
        info = mt5_data.connect()
        self.assertTrue(info.connected)
        self.assertEqual(mt5_data._STATE["found"], "C:/good/terminal64.exe")
        tried = [call[0][0] if call[0] else "" for call in self.fake.init_calls]
        self.assertEqual(tried, ["", "", "C:/bad/terminal64.exe", "C:/bad/terminal64.exe", "C:/good/terminal64.exe"])

    def test_initialize_failure_and_disconnected_terminal(self):
        self.fake = FakeMT5(init_ok=False)
        with self.assertRaises(mt5_data.MT5Error) as caught:
            mt5_data.connect()
        self.assertEqual(caught.exception.code, "init_failed")
        self.assertIn("-10003", caught.exception.message)
        self.fake = FakeMT5(connected=False)
        with self.assertRaises(mt5_data.MT5Error) as caught:
            mt5_data.connect()
        self.assertEqual(caught.exception.code, "not_connected")
        self.assertGreaterEqual(self.fake.shutdown_calls, 1)

    def test_lost_terminal_is_reconnected(self):
        mt5_data.connect()
        self.fake.connected = False
        with self.assertRaises(mt5_data.MT5Error):
            mt5_data.connect()
        self.fake.connected = True
        mt5_data.connect()
        self.assertEqual(len(self.fake.init_calls), 3)

    def test_missing_or_empty_quotes(self):
        self.fake = FakeMT5(bid=0.0, ask=0.0)
        with self.assertRaises(mt5_data.MT5Error) as caught:
            mt5_data.fetch_symbol("EURUSD", ["H1"])
        self.assertEqual(caught.exception.code, "no_tick")

    def test_no_candles_and_partial_timeframes(self):
        self.fake = FakeMT5(rates_ok=False)
        with self.assertRaises(mt5_data.MT5Error) as caught:
            mt5_data.fetch_symbol("EURUSD", ["H1"])
        self.assertEqual(caught.exception.code, "no_rates")
        self.fake = FakeMT5()
        self.fake.dead_timeframes = {FakeMT5.TIMEFRAME_H4}
        raw = mt5_data.fetch_symbol("EURUSD", ["H1", "H4"])
        self.assertEqual(set(raw["rates"]), {"H1"})
        self.assertEqual(raw["failed_timeframes"], ["H4"])
        self.fake = FakeMT5(bars=20)
        with self.assertRaises(mt5_data.MT5Error):   # fewer than MIN_BARS usable candles
            mt5_data.fetch_symbol("EURUSD", ["H1"])

    def test_missing_package_is_reported_in_persian(self):
        def missing():
            raise mt5_data.MT5Error("پکیج MetaTrader5 نصب نیست", "not_installed")

        with patch.object(mt5_data, "_import_mt5", missing):
            self.assertFalse(mt5_data.is_installed())
            with self.assertRaises(mt5_data.MT5Error) as caught:
                mt5_data.connect()
            self.assertEqual(caught.exception.code, "not_installed")
            self.assertEqual(mt5_data.peek()["installed"], False)

    def test_peek_never_starts_the_terminal(self):
        state = mt5_data.peek()
        self.assertEqual(state, {"installed": True, "connected": False, "busy": False})
        self.assertEqual(self.fake.init_calls, [])
        mt5_data.connect()
        self.assertTrue(mt5_data.peek()["connected"])

    def test_summary_hides_login_and_balance(self):
        report = mt5_data.summary()
        self.assertEqual(report["server"], "Demo-Server")
        self.assertEqual(report["account_type"], "دمو")
        self.assertEqual(report["ping_ms"], 24.5)
        self.assertEqual(report["sample"]["symbol"], "EURUSD")
        text = repr(report)
        for secret in ("123456", "1000.0", "Secret Name"):
            self.assertNotIn(secret, text)

    def test_shutdown_releases_the_terminal_once(self):
        mt5_data.connect()
        mt5_data.shutdown()
        mt5_data.shutdown()
        self.assertEqual(self.fake.shutdown_calls, 1)

    def test_every_mt5_call_runs_on_the_same_dedicated_thread(self):
        threads = set()
        original = self.fake.symbol_info_tick

        def tracking(name):
            threads.add(threading.current_thread().name)
            return original(name)

        self.fake.symbol_info_tick = tracking
        results = []
        workers = [threading.Thread(target=lambda: results.append(mt5_data.fetch_symbol("EURUSD", ["H1"])))
                   for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertEqual(len(results), 4)
        self.assertEqual(threads, {"mt5-worker"})
        self.assertEqual(len(self.fake.init_calls), 1)

    def test_a_hung_terminal_times_out_and_does_not_block_the_ui_status(self):
        release = threading.Event()
        original = self.fake.symbol_info_tick

        def slow(name):
            release.wait(5)
            return original(name)

        self.fake.symbol_info_tick = slow
        mt5_data.connect()                                   # ready, so peek has something to ask
        errors = []

        def run():
            try:
                with patch.object(mt5_data, "CALL_TIMEOUT_SECONDS", 0.2):
                    mt5_data._WORKER.call(mt5_data._fetch_impl, "EURUSD", ["H1"], 300, "", "", timeout=0.2)
            except mt5_data.MT5Error as exc:
                errors.append(exc.code)

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.05)
        started = time.monotonic()
        state = mt5_data.peek()
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(state["busy"])
        thread.join(5)
        self.assertEqual(errors, ["timeout"])
        release.set()
        time.sleep(0.2)                                       # let the worker finish the abandoned job
        self.assertFalse(mt5_data.peek()["busy"])

    def test_errors_from_the_worker_keep_their_type(self):
        self.fake = FakeMT5(init_ok=False)
        with self.assertRaises(mt5_data.MT5Error) as caught:
            mt5_data.fetch_symbol("EURUSD", ["H1"])
        self.assertEqual(caught.exception.code, "init_failed")

    def test_module_never_places_orders(self):
        source = Path(mt5_data.__file__).read_text(encoding="utf-8")
        for forbidden in ("order_send", "order_check", "positions_close", "TRADE_ACTION"):
            self.assertNotIn(forbidden, source)


class ForexSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.dict(os.environ, {"LOCALAPPDATA": self.directory.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults(self):
        self.assertEqual(app_settings.forex_settings(),
                         {"enabled": True, "terminal_path": "", "symbol_suffix": "", "default_timeframe": "M15",
                          "use_vision": True, "vision_model": "", "text_model": "",
                          "news_enabled": True, "news_url": ""})

    def test_update_is_partial_and_persistent(self):
        app_settings.set_forex_settings({"enabled": False})
        app_settings.set_forex_settings({"symbol_suffix": " .a "})
        saved = app_settings.forex_settings()
        self.assertEqual((saved["enabled"], saved["terminal_path"], saved["symbol_suffix"]), (False, "", ".a"))
        app_settings.set_forex_settings({"symbol_suffix": ""})
        self.assertEqual(app_settings.forex_settings()["symbol_suffix"], "")

    def test_invalid_values_are_rejected(self):
        for bad in ({"enabled": "yes"}, {"symbol_suffix": "a b"}, {"symbol_suffix": "x" * 13},
                    {"symbol_suffix": 5}, {"terminal_path": 5}, {"terminal_path": "relative/terminal64.exe"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                app_settings.set_forex_settings(bad)
        with self.assertRaises(ValueError):
            app_settings.set_forex_settings([])

    def test_terminal_path_must_be_an_existing_terminal64_exe(self):
        folder = Path(self.directory.name)
        good = folder / "terminal64.exe"
        good.write_bytes(b"")
        other = folder / "notepad.exe"
        other.write_bytes(b"")
        with self.assertRaises(ValueError):
            app_settings.set_forex_settings({"terminal_path": str(other)})
        with self.assertRaises(ValueError):
            app_settings.set_forex_settings({"terminal_path": str(folder / "missing" / "terminal64.exe")})
        saved = app_settings.set_forex_settings({"terminal_path": f'"{good}"'})
        self.assertEqual(saved["terminal_path"], str(good))
        self.assertEqual(app_settings.set_forex_settings({"terminal_path": ""})["terminal_path"], "")


class ForexServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.dict(os.environ, {"LOCALAPPDATA": self.directory.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, text, history=()):
        return list(history) + [{"role": "user", "content": text}]

    def test_success_builds_context_and_notices(self):
        with patch.object(mt5_data, "fetch_symbol", side_effect=lambda symbol, *a, **k: make_raw(symbol)) as fetch:
            result = forex_service.build_context(self.ask("EURUSD رو تحلیل کن"), 6000, now=NOW)
        self.assertTrue(result.ok)
        self.assertIn("== EURUSD", result.context)
        self.assertTrue(result.context.startswith("[تحلیل کمّی تولیدشده با کد از داده‌ی MetaTrader 5"))
        self.assertLessEqual(len(result.context), 6000)
        self.assertIn("داده‌ی زنده‌ی MT5 برای EURUSD دریافت شد", result.notices[0])
        self.assertEqual(fetch.call_args.args[1], ["M5", "M15", "H1", "H4", "D1"])   # default trading frame M15
        self.assertEqual(fetch.call_args.kwargs["bars"]["M15"], mt5_data.DEEP_BARS)

    def test_requested_timeframes_and_settings_reach_the_data_layer(self):
        app_settings.set_forex_settings({"symbol_suffix": ".a"})
        with patch.object(mt5_data, "fetch_symbol", side_effect=lambda symbol, *a, **k: make_raw(symbol)) as fetch:
            forex_service.build_context(self.ask("gbpusd 5m"), 6000, now=NOW)
        self.assertEqual(fetch.call_args.args[1], ["M1", "M5", "M15", "H1", "H4"])   # 5m = trading frame
        self.assertEqual(fetch.call_args.kwargs["suffix"], ".a")

    def test_weekend_and_reversed_pair_notices(self):
        with patch.object(mt5_data, "fetch_symbol", side_effect=lambda symbol, *a, **k: make_raw(
                symbol, now_utc=SATURDAY, tick_age=40 * 3600)):
            result = forex_service.build_context(self.ask("USDEUR"), 6000, now=SATURDAY)
        joined = " ".join(result.notices)
        self.assertIn("آخر هفته بسته است", joined)
        self.assertIn("ترتیب استاندارد بازار (EURUSD)", joined)
        self.assertIn("بازار آخر هفته بسته است", result.context)

    def test_terminal_problem_gives_a_no_invented_numbers_context(self):
        error = mt5_data.MT5Error("ترمینال بسته است", "init_failed")
        with patch.object(mt5_data, "fetch_symbol", side_effect=error) as fetch:
            result = forex_service.build_context(self.ask("EURUSD و GBPUSD تحلیل"), 6000, now=NOW)
        self.assertFalse(result.ok)
        self.assertEqual(fetch.call_count, 1)                   # the second symbol would fail the same way
        self.assertIn("EURUSD", result.context)
        self.assertIn("GBPUSD", result.context)
        self.assertIn("هیچ قیمت", result.context)
        self.assertEqual(len(result.notices), 2)
        self.assertTrue(all("داده‌ی MT5" in notice for notice in result.notices))

    def test_one_failing_symbol_does_not_hide_the_other(self):
        def fetch(symbol, *args, **kwargs):
            if symbol == "EURUSD":
                raise mt5_data.MT5Error("نماد پیدا نشد", "symbol_not_found")
            return make_raw(symbol)

        with patch.object(mt5_data, "fetch_symbol", side_effect=fetch):
            result = forex_service.build_context(self.ask("EURUSD GBPUSD"), 6000, now=NOW)
        self.assertTrue(result.ok)
        self.assertIn("== GBPUSD", result.context)
        self.assertIn("[بدون داده] EURUSD", result.context)
        self.assertTrue(any("EURUSD دریافت نشد" in notice for notice in result.notices))

    def test_unexpected_errors_never_escape(self):
        with patch.object(mt5_data, "fetch_symbol", side_effect=RuntimeError("boom")):
            result = forex_service.build_context(self.ask("EURUSD"), 6000, now=NOW)
        self.assertFalse(result.ok)
        self.assertIn("RuntimeError", result.context)
        self.assertNotIn("boom", result.context)

    def test_too_little_history_is_reported(self):
        raw = make_raw(timeframes=("H1",), n=300)
        raw["rates"]["H1"] = {key: values[-10:] for key, values in raw["rates"]["H1"].items()}
        with patch.object(mt5_data, "fetch_symbol", return_value=raw):
            result = forex_service.build_context(self.ask("EURUSD"), 6000, now=NOW)
        self.assertFalse(result.ok)
        self.assertIn("کافی نبود", result.context)

    def test_follow_up_uses_previous_pair_and_says_so(self):
        history = [{"role": "user", "content": "EURUSD"}, {"role": "assistant", "content": "تحلیل ..."}]
        with patch.object(mt5_data, "fetch_symbol", side_effect=lambda symbol, *a, **k: make_raw(symbol)):
            result = forex_service.build_context(self.ask("سطح حمایت چنده؟", history), 6000, now=NOW)
        self.assertTrue(result.ok)
        self.assertTrue(any("از پیام قبلی" in notice for notice in result.notices))

    def test_small_budget_is_respected(self):
        with patch.object(mt5_data, "fetch_symbol", side_effect=lambda symbol, *a, **k: make_raw(symbol)):
            result = forex_service.build_context(self.ask("EURUSD GBPUSD USDJPY"), 2600, now=NOW)
        self.assertLessEqual(len(result.context), 2600)

    def test_wants_forex_follows_the_setting(self):
        self.assertTrue(forex_service.wants_forex(self.ask("EURUSD")))
        self.assertFalse(forex_service.wants_forex(self.ask("سلام")))
        app_settings.set_forex_settings({"enabled": False})
        self.assertFalse(forex_service.wants_forex(self.ask("EURUSD")))

    def test_prompt_forbids_invented_numbers_and_advice(self):
        prompt = forex_service.FOREX_PROMPT
        for phrase in ("چیزی از خودت نساز", "توصیهٔ مالی", "تقویم اقتصادی"):
            self.assertIn(phrase, prompt)


if __name__ == "__main__":
    unittest.main()
