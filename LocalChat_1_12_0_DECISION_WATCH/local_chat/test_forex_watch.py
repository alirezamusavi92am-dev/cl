import os
os.environ.setdefault("LOCALCHAT_OFFLINE", "1")
import copy
import json
import tempfile
import unittest
from types import SimpleNamespace

from local_chat import forex_decision, forex_watch, mt5_data

CFG = {"terminal_path": "", "symbol_suffix": ""}


def scenario(direction="long", ev=0.4, p=0.5, trigger=1.1010, stop=1.0980, target=1.1060):
    return {"direction": direction, "entry": 1.1000, "stop": stop, "target1": target, "target2": 1.1100,
            "rr1": 2.0, "trigger": trigger, "expectancy_net_r": ev, "odds": {"target": p}}


def report(grade="قابل اتکا", lean="up", scenarios=None, market="open", news=None, vision=None, close=1.1000):
    return {
        "symbol": "EURUSD", "timeframe": "M15", "lean": lean, "news": news, "vision": vision,
        "snapshot": {"digits": 5, "market": market, "spread": 0.00002,
                     "timeframes": {"M15": {"close": close}}},
        "quant": {"grade": grade, "grade_text": "t", "atr": 0.0010, "horizon_bars": 16},
        "scenarios": [scenario()] if scenarios is None else scenarios,
    }


class DecisionTests(unittest.TestCase):
    def test_all_gates_pass_gives_a_conditional_plan_with_steps(self):
        d = forex_decision.decide(report())
        self.assertEqual(d["action"], "plan")
        self.assertTrue(d["auto_ok"])
        self.assertEqual(d["plan"]["direction"], "long")
        self.assertEqual(d["plan"]["expires_bars"], 8)
        self.assertTrue(any("1.10100" in step for step in d["steps"]))
        self.assertIn("الان وارد نشو", d["steps"][0])

    def test_no_edge_and_unclear_lean_mean_no_trade(self):
        self.assertEqual(forex_decision.decide(report(grade="بدون لبه"))["action"], "no_trade")
        self.assertEqual(forex_decision.decide(report(grade="ناکافی"))["action"], "no_trade")
        self.assertEqual(forex_decision.decide(report(lean="range"))["action"], "no_trade")
        self.assertEqual(forex_decision.decide(report(lean="balanced"))["action"], "no_trade")
        self.assertEqual(forex_decision.decide(report(scenarios=[]))["action"], "no_trade")
        quant_missing = report()
        quant_missing["quant"] = None
        self.assertEqual(forex_decision.decide(quant_missing)["action"], "no_trade")

    def test_blockers_downgrade_to_watch_and_disable_auto(self):
        cases = [report(grade="ضعیف"), report(market="closed"),
                 report(scenarios=[scenario(ev=0.02)]), report(scenarios=[scenario(p=0.2)]),
                 report(news={"ok": True, "imminent": {"title": "x"}}), report(vision={"agreement": "conflict"})]
        for case in cases:
            d = forex_decision.decide(case)
            self.assertEqual(d["action"], "watch")
            self.assertFalse(d["auto_ok"])
            self.assertTrue(d["blockers"])
            self.assertIsNotNone(d["plan"])

    def test_short_direction_and_fallback_trigger(self):
        d = forex_decision.decide(report(lean="down", scenarios=[scenario("short", trigger=None, stop=1.1020, target=1.0940)]))
        self.assertEqual(d["plan"]["direction"], "short")
        self.assertAlmostEqual(d["plan"]["trigger"], 1.1000 - 0.25 * 0.0010)

    def test_trigger_and_invalidation(self):
        plan = forex_decision.decide(report())["plan"]
        self.assertTrue(forex_decision.trigger_hit(plan, 1.1011))
        self.assertFalse(forex_decision.trigger_hit(plan, 1.1009))
        self.assertTrue(forex_decision.invalidated(plan, 1.0979))
        short = dict(plan, direction="short", trigger=1.0990, invalidation=1.1020)
        self.assertTrue(forex_decision.trigger_hit(short, 1.0989))
        self.assertTrue(forex_decision.invalidated(short, 1.1021))

    def test_position_volume(self):
        # 10 000 equity, 0.5% = 50 risk; 30 pips stop = 0.0030 → 300 ticks of 1e-5 at 1 per tick-lot → 0.1666 → 0.16
        self.assertEqual(forex_decision.position_volume(10000, 0.5, 0.0030, 1e-5, 1.0, 0.01, 100, 0.01), 0.16)
        self.assertIsNone(forex_decision.position_volume(100, 0.5, 0.0030, 1e-5, 1.0, 0.01, 100, 0.01))
        self.assertIsNone(forex_decision.position_volume(10000, 0.5, 0, 1e-5, 1.0, 0.01, 100, 0.01))
        self.assertEqual(forex_decision.position_volume(10 ** 9, 1, 0.0030, 1e-5, 1.0, 0.01, 50, 0.01), 50)


class FakeTrade:
    def __init__(self, mode="demo", allowed=True, positions=(), today=None):
        self.mode, self.allowed, self.positions = mode, allowed, list(positions)
        self.today = today or {"pnl": 0.0, "trades": 0}
        self.sent = []

    def account_state(self, path):
        return {"mode": self.mode, "equity": 10000.0, "balance": 10000.0, "currency": "USD", "login": 1,
                "account_trade_allowed": self.allowed, "terminal_trade_allowed": self.allowed}

    def own_positions(self, *args):
        return self.positions

    def today_result(self, *args):
        return self.today

    def symbol_trade_info(self, *args):
        return {"tick_size": 1e-5, "tick_value": 1.0, "vol_min": 0.01, "vol_max": 100, "vol_step": 0.01}

    def send_market_order(self, *args):
        self.sent.append(args)
        return {"ticket": 5, "price": 1.1011, "volume": args[3]}


class WatcherTests(unittest.TestCase):
    def make(self, reports, mode="demo", trade=None, live_ok=False):
        queue = list(reports)
        self.logs = []
        self.dir = tempfile.mkdtemp()
        log = SimpleNamespace(event=lambda kind, message, **data: self.logs.append(kind))
        watcher = forex_watch.Watcher(
            "EURUSD", "M15", CFG, mode=mode, stop_file=os.path.join(self.dir, "STOP"),
            state_path=os.path.join(self.dir, "s.json"), log=log, analyze=lambda: self.with_decision(queue.pop(0)),
            trade=trade or FakeTrade(), clock=lambda: 1_000_000.0)
        watcher.live_ok = live_ok
        return watcher

    @staticmethod
    def with_decision(rep):
        rep = copy.deepcopy(rep)
        rep["decision"] = forex_decision.decide(rep)
        return rep

    def test_scenario_arms_then_triggers_and_places_an_order(self):
        trade = FakeTrade()
        w = self.make([report(), report(close=1.1004), report(close=1.1012)], trade=trade)
        self.assertEqual(w.step(), "armed")
        self.assertEqual(w.step(), "waiting")
        self.assertEqual(w.step(), "order")
        self.assertEqual(len(trade.sent), 1)
        symbol, suffix, direction, volume, stop, target, magic = trade.sent[0][:7]
        self.assertEqual((direction, stop, target, magic), ("long", 1.0980, 1.1060, forex_watch.MAGIC))
        self.assertEqual(volume, 0.25)           # 50 risk / (0.0020 / 1e-5 = 200 ticks)
        self.assertIsNone(w.state["plan"])

    def test_alert_mode_never_sends_orders(self):
        trade = FakeTrade()
        w = self.make([report(), report(close=1.1012)], mode="alert", trade=trade)
        w.step()
        self.assertEqual(w.step(), "signal")
        self.assertEqual(trade.sent, [])

    def test_invalidation_expiry_and_changed_conditions_cancel(self):
        w = self.make([report(), report(close=1.0970)])
        w.step()
        self.assertEqual(w.step(), "cancelled")
        w = self.make([report()] + [report(close=1.1003)] * 8)
        w.step()
        results = [w.step() for _ in range(8)]
        self.assertEqual(results[-1], "expired")
        w = self.make([report(), report(grade="ضعیف", close=1.1012)])
        w.step()
        self.assertEqual(w.step(), "rejected")

    def test_order_gates(self):
        confirm = [report(), report(close=1.1012)]
        for trade, mode, live in (
            (FakeTrade(mode="real"), "demo", False),                    # demo mode on a real account
            (FakeTrade(mode="real"), "live", False),                    # live without the explicit flag
            (FakeTrade(allowed=False), "demo", False),                  # algo trading off
            (FakeTrade(positions=[{"ticket": 1}]), "demo", False),      # already in a trade
            (FakeTrade(today={"pnl": 0, "trades": 3}), "demo", False),  # trade cap
            (FakeTrade(today={"pnl": -250.0, "trades": 1}), "demo", False),  # daily loss cap
        ):
            w = self.make(confirm, mode=mode, trade=trade, live_ok=live)
            w.step()
            self.assertEqual(w.step(), "refused", (trade.mode, mode))
            self.assertEqual(trade.sent, [])
        trade = FakeTrade(mode="real")
        w = self.make(confirm, mode="live", trade=trade, live_ok=True)
        w.step()
        self.assertEqual(w.step(), "order")

    def test_stop_file_blocks_and_state_survives_restart(self):
        trade = FakeTrade()
        w = self.make([report(), report(close=1.1012)], trade=trade)
        w.step()
        open(os.path.join(self.dir, "STOP"), "w").close()
        self.assertEqual(w.step(), "refused")
        w2 = self.make([report()], trade=trade)
        w2.state_path = os.path.join(self.dir, "s.json")
        w.state.update(plan=forex_decision.decide(report())["plan"], age=2)
        w._save()
        w3 = forex_watch.Watcher("EURUSD", "M15", CFG, state_path=w.state_path, log=w.log, analyze=lambda: None)
        self.assertEqual(w3.state["age"], 2)
        self.assertEqual(w3.state["plan"]["direction"], "long")

    def test_loop_runs_once_per_new_candle_and_survives_errors(self):
        times = iter([1000.0, 1010.0, 1020.0, 1900.0, 1910.0, 1910.0, 1910.0, 1910.0])
        calls = []

        def analyze():
            calls.append(1)
            if len(calls) == 2:
                raise mt5_data.MT5Error("قطع شد", "x")
            return self.with_decision(report(lean="range"))

        w = self.make([])
        w.analyze = analyze
        w.clock = lambda: next(times)
        w.run(sleep=lambda s: None, max_cycles=2)
        self.assertEqual(len(calls), 2)
        self.assertIn("error", self.logs)


if __name__ == "__main__":
    unittest.main()
