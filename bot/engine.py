"""Deterministic trading rules. The LLM proposes trades; this module is the referee.

Every limit that used to live only in a prompt (position caps, cash buffer,
trade counts, day-trade flatten) is enforced here in code.
"""
import math
import random
import string

from .markets import bucket_of, universe

MAX_SWING_TRADES = 3
MAX_DAYTRADE_OPENS = 2
SWING_POSITION_CAP = 0.20
DAYTRADE_POSITION_CAP = 0.10
MIN_CASH_FRACTION = 0.05


def nav_of(state):
    total = state["cash"]
    for p in state["positions"].values():
        total += p["shares"] * p.get("lastPrice", p["avgCost"])
    return total


def bucket_values(state):
    out = {"growth": 0.0, "core": 0.0, "daytrade": 0.0}
    for p in state["positions"].values():
        out[p.get("bucket") or "core"] = out.get(p.get("bucket") or "core", 0.0) + p["shares"] * p.get("lastPrice", p["avgCost"])
    return out


def round_units(cfg, x):
    if cfg["whole_units"]:
        return math.floor(x)
    return math.floor(x * 1e6) / 1e6


def costs_of(cfg):
    return cfg.get("costs") or {"slip_bps": 0, "fee_per_unit": 0.0, "fee_pct": 0.0, "fee_min": 0.0, "fee_max_pct": None}


def fill_price(cfg, price, side):
    """Quote adjusted for slippage: buys fill higher, sells fill lower."""
    slip = costs_of(cfg)["slip_bps"] / 10000.0
    return price * (1 + slip) if side == "buy" else price * (1 - slip)


def fee_of(cfg, units, fill):
    c = costs_of(cfg)
    value = units * fill
    fee = units * c["fee_per_unit"] + value * c["fee_pct"] / 100.0
    fee = max(fee, c["fee_min"]) if units > 0 else 0.0
    if c.get("fee_max_pct"):
        fee = min(fee, value * c["fee_max_pct"] / 100.0)
    return round(fee, 4)


def _trade_id(date_local, ticker):
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{date_local}-{ticker}-{rand}"


def mark_to_market(state, prices):
    for t, p in state["positions"].items():
        r = prices.get(t)
        if r and r.get("ok"):
            p["lastPrice"] = r["price"]


def _sell(state, cfg, ticker, shares, price, now_iso, date_local, rationale, sentiment):
    pos = state["positions"][ticker]
    shares = min(shares, pos["shares"])
    if shares <= 0:
        return None
    quote = price
    price = fill_price(cfg, quote, "sell")
    fee = fee_of(cfg, shares, price)
    proceeds = shares * price - fee
    basis = pos["avgCost"] * shares
    pnl = proceeds - basis
    pct = (pnl / basis * 100) if basis else 0.0
    state["cash"] += proceeds
    pos["shares"] = round(pos["shares"] - shares, 8)
    bucket = pos.get("bucket")
    if pos["shares"] <= 1e-9:
        del state["positions"][ticker]
    trade = {
        "id": _trade_id(date_local, ticker),
        "ticker": ticker,
        "action": "sell",
        "shares": shares,
        "price": round(price, 6),
        "refPrice": quote,
        "fee": fee,
        "date": now_iso,
        "bucket": bucket,
        "rationale": rationale,
        "realizedPnL": round(pnl, 2),
        "realizedPnLPct": round(pct, 2),
    }
    if sentiment:
        trade["sentimentSummary"] = sentiment
    return trade


def forced_flatten(state, cfg, prices, now_iso, date_local, is_last):
    """Sell day-trade positions that must not be held: everything at the last
    session of the day, and anything opened on an earlier day."""
    trades, notes = [], []
    for ticker, pos in list(state["positions"].items()):
        if pos.get("bucket") != "daytrade":
            continue
        if not (is_last or pos.get("openedDate") != date_local):
            continue
        r = prices.get(ticker)
        if not (r and r.get("ok")):
            notes.append(f"Could not flatten {ticker}: no price this session.")
            continue
        why = "Mandatory end-of-day flatten - day-trade positions are never held overnight." if is_last else "Overdue day-trade position closed at first opportunity (should not carry across days)."
        t = _sell(state, cfg, ticker, pos["shares"], r["price"], now_iso, date_local, why, None)
        if t:
            trades.append(t)
    return trades, notes


def apply_decisions(state, cfg, prices, decisions, now_iso, date_local, is_last):
    """Validate and execute LLM-proposed trades. Returns (executed, rejected_notes)."""
    executed, rejected = [], []
    swing_count = 0
    dt_opens = 0
    nav = nav_of(state)
    allowed = set(universe(cfg))

    # Sells first so freed cash is available to the buys.
    ordered = sorted(decisions, key=lambda d: 0 if str(d.get("action", "")).lower() == "sell" else 1)
    for d in ordered:
        action = str(d.get("action", "")).lower()
        ticker = d.get("ticker")
        if ticker not in allowed:
            rejected.append(f"{ticker}: not on the watchlist")
            continue
        r = prices.get(ticker)
        if not (r and r.get("ok")):
            rejected.append(f"{ticker}: no price this session")
            continue
        price = r["price"]
        rationale = (d.get("rationale") or "").strip() or "No rationale given."
        sentiment = (d.get("sentiment") or "").strip() or None

        if action == "sell":
            pos = state["positions"].get(ticker)
            if not pos:
                rejected.append(f"SELL {ticker}: not held")
                continue
            is_dt = pos.get("bucket") == "daytrade"
            if not is_dt and swing_count >= MAX_SWING_TRADES:
                rejected.append(f"SELL {ticker}: swing trade limit reached")
                continue
            want = d.get("shares", "all")
            shares = pos["shares"] if want in ("all", None) else min(float(want), pos["shares"])
            shares = round_units(cfg, shares) if want not in ("all", None) else pos["shares"]
            t = _sell(state, cfg, ticker, shares, price, now_iso, date_local, rationale, sentiment)
            if t:
                executed.append(t)
                if not is_dt:
                    swing_count += 1
            continue

        if action != "buy":
            rejected.append(f"{ticker}: unknown action {action!r}")
            continue

        want_dt = str(d.get("bucket", "")).lower() == "daytrade"
        pos = state["positions"].get(ticker)
        if want_dt:
            if is_last:
                rejected.append(f"BUY {ticker}: no new day-trades in the last session")
                continue
            if dt_opens >= MAX_DAYTRADE_OPENS:
                rejected.append(f"BUY {ticker}: day-trade open limit reached")
                continue
            if pos and pos.get("bucket") != "daytrade":
                rejected.append(f"BUY {ticker}: already held as a swing position")
                continue
            bucket, cap = "daytrade", DAYTRADE_POSITION_CAP
        else:
            if swing_count >= MAX_SWING_TRADES:
                rejected.append(f"BUY {ticker}: swing trade limit reached")
                continue
            if pos and pos.get("bucket") == "daytrade":
                rejected.append(f"BUY {ticker}: already held as a day-trade position")
                continue
            bucket, cap = bucket_of(cfg, ticker), SWING_POSITION_CAP

        try:
            usd = float(d.get("usd", 0))
        except (TypeError, ValueError):
            usd = 0.0
        held_value = pos["shares"] * pos.get("lastPrice", pos["avgCost"]) if pos else 0.0
        usd = min(usd, cap * nav - held_value, state["cash"] - MIN_CASH_FRACTION * nav)
        quote = price
        price = fill_price(cfg, quote, "buy")
        units = round_units(cfg, usd / price) if usd > 0 else 0
        for _ in range(6):  # the budget must also cover the fee
            if units <= 0 or units * price + fee_of(cfg, units, price) <= usd + 1e-9:
                break
            units = round_units(cfg, (usd - fee_of(cfg, units, price)) / price)
        if units <= 0:
            rejected.append(f"BUY {ticker}: nothing affordable within caps (cap {cap:.0%}, cash buffer {MIN_CASH_FRACTION:.0%})")
            continue
        fee = fee_of(cfg, units, price)
        cost = units * price + fee  # cost basis includes the fee
        state["cash"] -= cost
        if pos:
            total = pos["shares"] + units
            pos["avgCost"] = (pos["shares"] * pos["avgCost"] + cost) / total
            pos["shares"] = round(total, 8)
            pos["lastPrice"] = quote
        else:
            state["positions"][ticker] = {"shares": units, "avgCost": cost / units, "lastPrice": quote, "bucket": bucket, "openedDate": date_local}
        horizon = (d.get("horizon") or "").strip() or ("Intraday - close by end of trading day" if bucket == "daytrade" else "Horizon not specified")
        trade = {
            "id": _trade_id(date_local, ticker),
            "ticker": ticker,
            "action": "buy",
            "shares": units,
            "price": round(price, 6),
            "refPrice": quote,
            "fee": fee,
            "date": now_iso,
            "bucket": bucket,
            "rationale": rationale,
            "horizon": horizon,
        }
        if sentiment:
            trade["sentimentSummary"] = sentiment
        executed.append(trade)
        if bucket == "daytrade":
            dt_opens += 1
        else:
            swing_count += 1
    return executed, rejected
