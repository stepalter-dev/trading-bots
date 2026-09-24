import copy
from bot import engine
from bot.markets import MARKETS

US_REAL, CRYPTO_REAL, ASX_REAL = MARKETS["us"], MARKETS["crypto"], MARKETS["asx"]
# rule tests run cost-free so the numbers stay round; cost maths is tested separately below
US = {**US_REAL, "costs": None}
CRYPTO = {**CRYPTO_REAL, "costs": None}
P = lambda price: {"ok": True, "price": price}


def fresh(cash=10000.0):
    return {"cash": cash, "startingCash": cash, "positions": {}}


def test_buy_capped_at_20pct_and_whole_shares():
    s = fresh()
    ex, rej = engine.apply_decisions(s, US, {"AAPL": P(100)}, [{"action": "buy", "ticker": "AAPL", "usd": 9000}], "t", "d", False)
    assert s["positions"]["AAPL"]["shares"] == 20  # 20% of 10k = 2000 -> 20 shares
    assert s["cash"] == 8000


def test_cash_buffer_kept():
    s = fresh()
    s["positions"] = {"MSFT": {"shares": 40, "avgCost": 100, "lastPrice": 100, "bucket": "core"}}
    s["cash"] = 6000  # NAV 10k
    engine.apply_decisions(s, US, {"AAPL": P(10)}, [{"action": "buy", "ticker": "AAPL", "usd": 5000}], "t", "d", False)
    assert s["cash"] >= 0.05 * 10000 - 1e-6


def test_unknown_ticker_and_missing_price_rejected():
    s = fresh()
    ex, rej = engine.apply_decisions(s, US, {"AAPL": {"ok": False}}, [
        {"action": "buy", "ticker": "ZZZZ", "usd": 500}, {"action": "buy", "ticker": "AAPL", "usd": 500}], "t", "d", False)
    assert not ex and len(rej) == 2


def test_swing_trade_limit():
    s = fresh(100000)
    prices = {t: P(10) for t in ["AAPL", "MSFT", "JPM", "V"]}
    ds = [{"action": "buy", "ticker": t, "usd": 1000} for t in prices]
    ex, rej = engine.apply_decisions(s, US, prices, ds, "t", "d", False)
    assert len(ex) == 3 and len(rej) == 1


def test_daytrade_rules_and_flatten():
    s = fresh(100000)
    ex, _ = engine.apply_decisions(s, US, {"AMD": P(100)}, [{"action": "buy", "ticker": "AMD", "bucket": "daytrade", "usd": 50000}], "t", "d1", False)
    assert s["positions"]["AMD"]["shares"] == 100  # 10% cap
    ex, _ = engine.apply_decisions(s, US, {"NVDA": P(100)}, [{"action": "buy", "ticker": "NVDA", "bucket": "daytrade", "usd": 5000}], "t", "d1", True)
    assert not ex  # no new day-trades in the last session
    flat, _ = engine.forced_flatten(s, US, {"AMD": P(110)}, "t", "d1", True)
    assert "AMD" not in s["positions"] and flat[0]["realizedPnL"] == 1000.0


def test_overnight_daytrade_is_flattened_next_day():
    s = fresh(100000)
    engine.apply_decisions(s, US, {"AMD": P(100)}, [{"action": "buy", "ticker": "AMD", "bucket": "daytrade", "usd": 5000}], "t", "d1", False)
    flat, _ = engine.forced_flatten(s, US, {"AMD": P(100)}, "t", "d2", False)
    assert flat and not s["positions"]


def test_sell_more_than_held_and_partial():
    s = fresh()
    s["positions"] = {"AAPL": {"shares": 10, "avgCost": 100, "lastPrice": 100, "bucket": "core"}}
    ex, _ = engine.apply_decisions(s, US, {"AAPL": P(120)}, [{"action": "sell", "ticker": "AAPL", "shares": 4}], "t", "d", False)
    assert s["positions"]["AAPL"]["shares"] == 6 and ex[0]["realizedPnL"] == 80.0
    ex, _ = engine.apply_decisions(s, US, {"AAPL": P(120)}, [{"action": "sell", "ticker": "AAPL", "shares": "all"}], "t", "d", False)
    assert "AAPL" not in s["positions"]


def test_crypto_fractional_units():
    s = fresh(45000)
    engine.apply_decisions(s, CRYPTO, {"BTC-USD": P(84000)}, [{"action": "buy", "ticker": "BTC-USD", "usd": 5000}], "t", "d", False)
    assert 0.05 < s["positions"]["BTC-USD"]["shares"] < 0.06


def test_average_cost_on_add():
    s = fresh(100000)
    engine.apply_decisions(s, US, {"AAPL": P(100)}, [{"action": "buy", "ticker": "AAPL", "usd": 5000}], "t", "d", False)
    engine.apply_decisions(s, US, {"AAPL": P(120)}, [{"action": "buy", "ticker": "AAPL", "usd": 6000}], "t", "d", False)
    p = s["positions"]["AAPL"]
    assert p["shares"] == 100 and abs(p["avgCost"] - ((50 * 100 + 50 * 120) / 100)) < 1e-9


def test_costs_reduce_cash_and_raise_cost_basis():
    s = fresh(100000)
    ex, _ = engine.apply_decisions(s, US_REAL, {"AAPL": P(100)}, [{"action": "buy", "ticker": "AAPL", "usd": 10000}], "t", "d", False)
    t = ex[0]
    assert t["price"] > 100 and t["refPrice"] == 100 and t["fee"] >= 1.0  # slipped up, min fee
    spent = 100000 - s["cash"]
    assert spent <= 10000 + 1e-6  # budget includes fee
    assert abs(spent - (t["shares"] * t["price"] + t["fee"])) < 1e-6
    assert s["positions"]["AAPL"]["avgCost"] > t["price"]  # fee is in the basis


def test_round_trip_at_flat_price_loses_money():
    for cfg, price, usd in ((US_REAL, 100, 10000), (ASX_REAL, 50, 10000), (CRYPTO_REAL, 84000, 10000)):
        s = fresh(50000)
        key = {"us": "AAPL", "asx": "CBA.AX", "crypto": "BTC-USD"}[cfg["key"]]
        engine.apply_decisions(s, cfg, {key: P(price)}, [{"action": "buy", "ticker": key, "usd": usd}], "t", "d", False)
        ex, _ = engine.apply_decisions(s, cfg, {key: P(price)}, [{"action": "sell", "ticker": key, "shares": "all"}], "t", "d", False)
        assert ex[0]["realizedPnL"] < 0 and s["cash"] < 50000, cfg["key"]


def test_fee_minimums_and_caps():
    assert engine.fee_of(ASX_REAL, 10, 10.0) == 6.0            # A$6 minimum on a tiny trade
    assert engine.fee_of(US_REAL, 1000, 100.0) == 5.0           # 1000 x $0.005
    assert engine.fee_of(US_REAL, 1, 1.0) == 0.01               # capped at 1% of value
    assert abs(engine.fee_of(CRYPTO_REAL, 1, 50000.0) - 130.0) < 1e-6  # 0.26%
