"""The core-satellite / trade-less / trend rules adopted on 2026-09-25."""
from bot import engine, learning
from bot.markets import MARKETS

US = {**MARKETS["us"], "costs": None}
UP = lambda price: {"ok": True, "price": price, "ma200": price * 0.9, "above_ma200": True}
DOWN = lambda price: {"ok": True, "price": price, "ma200": price * 1.1, "above_ma200": False}
PLAN = {"target": 130, "stop": 90, "prob": 45}


def buy(t, usd=1000, **kw):
    d = {"action": "buy", "ticker": t, "usd": usd, **PLAN}
    d.update(kw)
    return d


def fresh(cash=10000.0):
    return {"cash": cash, "startingCash": cash, "positions": {}}


def test_trend_filter_blocks_buys_below_the_200_day_average():
    s = fresh()
    ex, rej = engine.apply_decisions(s, US, {"AAPL": DOWN(100)}, [buy("AAPL")], "t", "2026-09-25", False)
    assert not ex and "below its 200-day average" in rej[0]
    ex, rej = engine.apply_decisions(s, US, {"AAPL": {"ok": True, "price": 100}}, [buy("AAPL")], "t", "2026-09-25", False)
    assert not ex and "no 200-day history" in rej[0]


def test_daytrading_is_paused():
    s = fresh()
    ex, rej = engine.apply_decisions(s, US, {"AMD": UP(100)}, [buy("AMD", bucket="daytrade", target=106, stop=96)], "t", "2026-09-25", False)
    assert not ex and "paused" in rej[0]


def test_minimum_hold_except_at_target():
    s = fresh()
    engine.apply_decisions(s, US, {"AAPL": UP(100)}, [buy("AAPL")], "t", "2026-09-20", False)
    sell = [{"action": "sell", "ticker": "AAPL", "shares": "all"}]
    ex, rej = engine.apply_decisions(s, US, {"AAPL": UP(110)}, sell, "t", "2026-09-25", False)
    assert not ex and "minimum hold" in rej[0]
    ex, rej = engine.apply_decisions(s, US, {"AAPL": UP(131)}, sell, "t", "2026-09-25", False)  # target reached
    assert ex and not rej
    engine.apply_decisions(s, US, {"MSFT": UP(100)}, [buy("MSFT")], "t", "2026-09-01", False)
    ex, rej = engine.apply_decisions(s, US, {"MSFT": UP(101)}, [{"action": "sell", "ticker": "MSFT", "shares": "all"}], "t", "2026-09-25", False)
    assert ex and not rej  # held 24 days


def test_turnover_cap_counts_the_last_30_days():
    s = fresh()
    history = [{"action": "buy", "bucket": "growth", "shares": 25, "price": 100, "date": "2026-09-10T14:00:00Z"},
               {"action": "buy", "bucket": "core", "shares": 50, "price": 100, "date": "2026-08-01T14:00:00Z"}]  # too old to count
    ex, rej = engine.apply_decisions(s, US, {"AAPL": UP(100)}, [buy("AAPL", usd=1000)], "2026-09-25T14:00:00Z", "2026-09-25", False, history=history)
    assert ex and ex[0]["shares"] == 5 and "turnover allowance" in rej[0]  # 30% of 10k = 3000, 2500 used
    ex, rej = engine.apply_decisions(s, US, {"MSFT": UP(100)}, [buy("MSFT")], "2026-09-25T15:00:00Z", "2026-09-25", False, history=history + ex)
    assert not ex and "turnover cap reached" in rej[0]


def test_anchor_fills_from_cash_then_trims_active_positions():
    s = {"cash": 3000.0, "startingCash": 10000.0,
         "positions": {"AAPL": {"shares": 50, "avgCost": 100, "lastPrice": 100, "bucket": "core"},
                       "NVDA": {"shares": 20, "avgCost": 100, "lastPrice": 100, "bucket": "growth"}}}
    prices = {"SPY": UP(500), "AAPL": UP(100), "NVDA": UP(100)}
    trades, _ = engine.rebalance_anchor(s, US, prices, "t", "2026-09-25")
    nav = engine.nav_of(s)
    assert abs(nav - 10000) < 1e-6  # no costs in this config
    assert abs(engine.anchor_value(s) / nav - 0.60) < 0.001
    assert abs(s["cash"] - 500) < 0.01  # 5% buffer kept
    sells = [t for t in trades if t["action"] == "sell"]
    assert len(sells) == 2 and all(t["exitReason"] == "rebalance" for t in sells)
    # pro rata: both trimmed by the same fraction (3500 of 7000 active -> half)
    assert s["positions"]["AAPL"]["shares"] == 25 and s["positions"]["NVDA"]["shares"] == 10
    # rebalance sells are not journaled as the bot's own trades
    data = {"state": s, "trades": trades, "nav_history": []}
    assert learning.ensure(data)["journal"] == []


def test_anchor_band_and_overweight_trim():
    s = {"cash": 1000.0, "startingCash": 10000.0, "positions": {},
         "anchor": {"ticker": "SPY", "shares": 12, "avgCost": 500, "lastPrice": 500}}
    s["positions"]["AAPL"] = {"shares": 30, "avgCost": 100, "lastPrice": 100, "bucket": "core"}  # NAV 10000, anchor 60%
    assert engine.rebalance_anchor(s, US, {"SPY": UP(520)}, "t", "d") == ([], [])  # 61.4% - inside the band
    trades, _ = engine.rebalance_anchor(s, US, {"SPY": UP(700)}, "t", "d")  # 8400 of 12400 = 67.7%
    assert trades and trades[0]["action"] == "sell" and trades[0]["bucket"] == "anchor"
    assert abs(engine.anchor_value(s) / engine.nav_of(s) - 0.60) < 0.001


def test_nav_and_buckets_include_the_anchor():
    s = {"cash": 100.0, "positions": {}, "anchor": {"ticker": "SPY", "shares": 2, "avgCost": 400, "lastPrice": 450}}
    assert engine.nav_of(s) == 1000 and engine.bucket_values(s)["anchor"] == 900
    engine.mark_to_market(s, {"SPY": UP(500)})
    assert engine.nav_of(s) == 1100


def test_benchmark_position_moves_into_the_anchor_without_trading():
    crypto = {**MARKETS["crypto"], "costs": None}
    s = {"cash": 3000.0, "startingCash": 10000.0,
         "positions": {"BTC-USD": {"shares": 0.05, "avgCost": 80000, "lastPrice": 80000, "bucket": "core"},
                       "SOL-USD": {"shares": 30, "avgCost": 100, "lastPrice": 100, "bucket": "growth"}}}
    trades, notes = engine.rebalance_anchor(s, crypto, {"BTC-USD": UP(80000), "SOL-USD": UP(100)}, "t", "d")
    assert "BTC-USD" not in s["positions"] and "Moved" in notes[0]
    assert not any(t["action"] == "sell" and t["ticker"] == "BTC-USD" for t in trades)
    assert abs(engine.anchor_value(s) / engine.nav_of(s) - 0.60) < 0.001
