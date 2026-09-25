import pytest
from bot import edge, engine, learning
from bot.markets import MARKETS

US = {**MARKETS["us"], "costs": None}
P = lambda price: {"ok": True, "price": price, "ma200": price * 0.9, "above_ma200": True}  # in an uptrend


@pytest.fixture(autouse=True)
def _daytrade_on(monkeypatch):
    """Day-trading is paused in production; these rule tests exercise it anyway."""
    monkeypatch.setattr(engine, "DAYTRADE_ENABLED", True)


def fresh(cash=10000.0):
    return {"cash": cash, "startingCash": cash, "positions": {}}


def buy(**kw):
    d = {"action": "buy", "ticker": "AAPL", "usd": 1500}
    d.update(kw)
    return d


def test_good_asymmetric_buy_passes_and_stores_plan():
    s = fresh()
    ex, rej = engine.apply_decisions(s, US, {"AAPL": P(100)}, [buy(target=130, stop=90, prob=45)], "t", "d", False)
    assert not rej and ex[0]["plan"]["rr"] == 3.0
    plan = s["positions"]["AAPL"]["plan"]
    assert plan["target"] == 130 and plan["stop"] == 90 and plan["prob"] == 0.45
    assert plan["evPct"] == round(0.45 * 30 - 0.55 * 10, 2)


def test_rejections():
    cases = [
        (buy(), "needs a numeric target"),                                   # no plan at all
        (buy(target=110, stop=90, prob=60), "reward:risk 1.00"),              # 1:1 swing
        (buy(target=120, stop=95, prob=10), "expected value"),               # 4:1 but negative EV
        (buy(target=99, stop=90, prob=60), "not above the price"),
        (buy(target=130, stop=101, prob=60), "must be below the price"),
        (buy(target=200, stop=50, prob=60), "stop is 50.0% away"),
        (buy(target=130, stop=90, prob=150), "needs a numeric"),            # 150% is not a probability
    ]
    for d, why in cases:
        s = fresh()
        ex, rej = engine.apply_decisions(s, US, {"AAPL": P(100)}, [d], "t", "d", False)
        assert not ex and why in rej[0], (d, rej)
        assert s["cash"] == 10000.0


def test_daytrade_has_looser_rr_but_tighter_stop():
    ok = buy(ticker="AMD", bucket="daytrade", target=106, stop=96, prob=55)       # 1.5:1
    s = fresh()
    ex, rej = engine.apply_decisions(s, US, {"AMD": P(100)}, [ok], "t", "d", False)
    assert ex and not rej
    wide = buy(ticker="AMD", bucket="daytrade", target=140, stop=85, prob=55)     # 15% stop
    s = fresh()
    ex, rej = engine.apply_decisions(s, US, {"AMD": P(100)}, [wide], "t", "d", False)
    assert not ex and "max 10%" in rej[0]


def test_prob_formats():
    assert edge.parse_prob("60%") == 0.6 and edge.parse_prob(0.6) == 0.6 and edge.parse_prob(60) == 0.6
    assert edge.parse_prob(0) is None and edge.parse_prob("x") is None


def test_costs_can_sink_a_marginal_trade():
    asx = MARKETS["asx"]  # A$6 minimum fee each way hurts a tiny position
    plan, why = edge.evaluate(asx, 10.0, 10.3, 9.9, 0.3, "swing", 100.0)
    assert plan is None and "expected value" in why


def test_stop_exit_sells_automatically():
    s = fresh()
    engine.apply_decisions(s, US, {"AAPL": P(100)}, [buy(target=130, stop=90, prob=45)], "t", "d", False)
    trades, _ = edge.stop_exits(s, US, {"AAPL": P(89)}, "t2", "d2", engine._sell)
    assert trades and trades[0]["exitReason"] == "stop" and "AAPL" not in s["positions"]
    s2 = fresh()
    engine.apply_decisions(s2, US, {"AAPL": P(100)}, [buy(target=130, stop=90, prob=45)], "t", "d", False)
    assert edge.stop_exits(s2, US, {"AAPL": P(91)}, "t2", "d2", engine._sell)[0] == []


def test_set_plans_for_existing_positions():
    s = fresh()
    s["positions"]["AAPL"] = {"shares": 5, "avgCost": 100, "lastPrice": 100, "bucket": "core"}
    notes = edge.set_plans(s, US, {"AAPL": P(100)}, [{"ticker": "AAPL", "target": 125, "stop": 92, "prob": 50},
                                                     {"ticker": "MSFT", "target": 1, "stop": 0.5, "prob": 50}])
    assert s["positions"]["AAPL"]["plan"]["rr"] == round(25 / 8, 2)
    assert notes and "MSFT" in notes[0]
    assert "NO PLAN" in edge.plan_line({"avgCost": 1}, 1, "$")
    assert "TARGET REACHED" in edge.plan_line(s["positions"]["AAPL"], 126, "$")


def test_calibration_in_scorecard():
    j = [{"prob": 0.6, "pnlPct": 5.0}, {"prob": 0.6, "pnlPct": -2.0}, {"prob": 0.3, "pnlPct": 9.0}, {"prob": None, "pnlPct": 1.0}]
    rows = learning.calibration(j)
    assert [(r[0], r[1]) for r in rows] == [("0%-40%", 1), ("55%-70%", 2)]
    assert rows[1][3] == 0.5
