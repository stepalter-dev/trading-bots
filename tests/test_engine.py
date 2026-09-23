import copy
from bot import engine
from bot.markets import MARKETS

US, CRYPTO = MARKETS["us"], MARKETS["crypto"]
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
