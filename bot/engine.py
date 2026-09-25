"""Deterministic trading rules. The LLM proposes trades; this module is the referee.

Every limit that used to live only in a prompt (position caps, cash buffer,
trade counts, day-trade flatten) is enforced here in code.
"""
import math
import random
import string

from .markets import bucket_of, universe

# Rules adopted 2026-09-25 after the backtest in BACKTEST.md (frozen until the quarterly review):
MAX_SWING_TRADES = 2          # fewer, better trades
MAX_DAYTRADE_OPENS = 2
DAYTRADE_ENABLED = False      # paused until the journal shows the day-trade sleeve has an edge
SWING_POSITION_CAP = 0.12     # of NAV (the active sleeve is ~35-40% of NAV)
DAYTRADE_POSITION_CAP = 0.10
MIN_CASH_FRACTION = 0.05
ANCHOR_TARGET = 0.60          # core-satellite: 60% of NAV held in the market's benchmark
ANCHOR_BAND = 0.05            # rebalance only when the anchor drifts outside 55-65%
MIN_HOLD_DAYS = 14            # swing positions are held at least 2 weeks (stop or target excepted)
TURNOVER_CAP = 0.30           # new swing buys per rolling 30 days, as a share of NAV
TREND_FILTER = True           # new swing buys only when the price is above its 200-day average


def anchor_value(state):
    a = state.get("anchor")
    return a["shares"] * a.get("lastPrice", a["avgCost"]) if a else 0.0


def nav_of(state):
    total = state["cash"] + anchor_value(state)
    for p in state["positions"].values():
        total += p["shares"] * p.get("lastPrice", p["avgCost"])
    return total


def bucket_values(state):
    out = {"growth": 0.0, "core": 0.0, "daytrade": 0.0, "anchor": anchor_value(state)}
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
    a = state.get("anchor")
    if a:
        r = prices.get(a["ticker"])
        if r and r.get("ok"):
            a["lastPrice"] = r["price"]


def rebalance_anchor(state, cfg, prices, now_iso, date_local):
    """Keep ANCHOR_TARGET of NAV in the benchmark (fractional units). Buys it with spare cash and,
    when cash is short, trims the active positions pro rata; sells it down when it has grown too big.
    Returns (trades, notes). Referee-initiated trades carry exitReason "rebalance"."""
    bench = cfg["benchmark"]
    r = prices.get(bench) or {}
    if not r.get("ok"):
        return [], ["Anchor not rebalanced: no benchmark price this session."]
    quote = r["price"]
    a = state.get("anchor")
    if a:
        a["lastPrice"] = quote
    nav = nav_of(state)
    share = anchor_value(state) / nav if nav else 0
    if ANCHOR_TARGET - ANCHOR_BAND <= share <= ANCHOR_TARGET + ANCHOR_BAND:
        return [], []
    trades, notes = [], []
    want = ANCHOR_TARGET * nav - anchor_value(state)
    held = state["positions"].get(bench)
    if want > 0 and held and held.get("bucket") != "daytrade":
        # an active position in the benchmark itself moves into the anchor at no cost (no sell-then-rebuy)
        units = min(held["shares"], round(want / quote, 8))
        if a:
            total = a["shares"] + units
            a["avgCost"] = (a["shares"] * a["avgCost"] + units * held["avgCost"]) / total
            a["shares"] = round(total, 8)
        else:
            a = state["anchor"] = {"ticker": bench, "shares": units, "avgCost": held["avgCost"], "lastPrice": quote}
        held["shares"] = round(held["shares"] - units, 8)
        if held["shares"] <= 1e-9:
            del state["positions"][bench]
        notes.append(f"Moved {units:g} {bench} from the active sleeve into the index anchor (no trade needed).")
        want = ANCHOR_TARGET * nav - anchor_value(state)
    if want > 1:
        spare = state["cash"] - MIN_CASH_FRACTION * nav
        short = want - max(spare, 0)
        active = {t: p for t, p in state["positions"].items() if p.get("bucket") != "daytrade"}
        active_val = sum(p["shares"] * p.get("lastPrice", p["avgCost"]) for p in active.values())
        if short > 1 and active_val > 0:
            frac = min(1.0, short / active_val)
            for t, p in list(active.items()):
                px = (prices.get(t) or {}).get("price") if (prices.get(t) or {}).get("ok") else None
                if not px:
                    continue
                units = round(p["shares"] * frac, 6)
                if units <= 0:
                    continue
                tr = _sell(state, cfg, t, units, px, now_iso, date_local,
                           "Rebalance: trimmed pro rata to fund the 60% index anchor (core-satellite rule).", None)
                if tr:
                    tr["exitReason"] = "rebalance"
                    trades.append(tr)
        budget = min(want, state["cash"] - MIN_CASH_FRACTION * nav_of(state))
        if budget <= 1:
            return trades, notes + ["Anchor below target but no spare cash."]
        fill = fill_price(cfg, quote, "buy")
        units = math.floor(budget / fill * 1e6) / 1e6
        fee = fee_of(cfg, units, fill)
        while units > 0 and units * fill + fee > budget:
            units = math.floor((budget - fee) / fill * 1e6) / 1e6
            fee = fee_of(cfg, units, fill)
        if units <= 0:
            return trades, notes
        cost = units * fill + fee
        state["cash"] -= cost
        if a:
            total = a["shares"] + units
            a["avgCost"] = (a["shares"] * a["avgCost"] + cost) / total
            a["shares"] = round(total, 8)
        else:
            state["anchor"] = {"ticker": bench, "shares": units, "avgCost": cost / units, "lastPrice": quote}
        trades.append({"id": _trade_id(date_local, bench), "ticker": bench, "action": "buy", "shares": units, "price": round(fill, 6),
                       "refPrice": quote, "fee": fee, "date": now_iso, "bucket": "anchor",
                       "rationale": f"Rebalance: topped the index anchor up to {ANCHOR_TARGET:.0%} of NAV (core-satellite rule).",
                       "horizon": "Permanent core holding"})
    elif want < -1:
        units = min(a["shares"], round(-want / quote, 6))
        fill = fill_price(cfg, quote, "sell")
        fee = fee_of(cfg, units, fill)
        proceeds = units * fill - fee
        pnl = proceeds - a["avgCost"] * units
        state["cash"] += proceeds
        a["shares"] = round(a["shares"] - units, 8)
        trades.append({"id": _trade_id(date_local, bench), "ticker": bench, "action": "sell", "shares": units, "price": round(fill, 6),
                       "refPrice": quote, "fee": fee, "date": now_iso, "bucket": "anchor", "exitReason": "rebalance",
                       "rationale": f"Rebalance: trimmed the index anchor back to {ANCHOR_TARGET:.0%} of NAV (core-satellite rule).",
                       "realizedPnL": round(pnl, 2), "realizedPnLPct": round(pnl / (a["avgCost"] * units) * 100, 2) if units else 0})
    return trades, notes


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


def _days_since(d1, d2):
    from datetime import date

    try:
        return (date.fromisoformat(d2[:10]) - date.fromisoformat(d1[:10])).days
    except (TypeError, ValueError):
        return 10 ** 6  # unknown open date -> treat as long held


def recent_swing_buys(history, date_local, days=30):
    return sum(t["shares"] * t["price"] for t in history or []
               if t.get("action") == "buy" and t.get("bucket") in ("growth", "core") and 0 <= _days_since(t["date"], date_local) < days)


def apply_decisions(state, cfg, prices, decisions, now_iso, date_local, is_last, history=None):
    """Validate and execute LLM-proposed trades. Returns (executed, rejected_notes).
    `history` is the market's past trade log (for the turnover cap)."""
    from . import edge  # local import: edge imports this module

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
            plan = pos.get("plan") or {}
            held = _days_since(pos.get("openedDate"), date_local)
            if not is_dt and held < MIN_HOLD_DAYS and not (plan.get("target") and price >= plan["target"]):
                rejected.append(f"SELL {ticker}: held {held} days; the minimum hold is {MIN_HOLD_DAYS} days unless the target is reached (the stop is automatic)")
                continue
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
        if want_dt and not DAYTRADE_ENABLED:
            rejected.append(f"BUY {ticker}: day-trading is paused until the journal shows the sleeve has an edge")
            continue
        if not want_dt and TREND_FILTER and r.get("above_ma200") is not True:
            why = "no 200-day history" if r.get("ma200") is None else f"price {price:g} is below its 200-day average {r['ma200']:.4g}"
            rejected.append(f"BUY {ticker}: trend filter - {why}")
            continue
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
        if bucket != "daytrade":
            room = TURNOVER_CAP * nav - recent_swing_buys((history or []) + executed, date_local)
            if room < usd:
                if room <= 0:
                    rejected.append(f"BUY {ticker}: 30-day turnover cap reached ({TURNOVER_CAP:.0%} of NAV in new buys)")
                    continue
                rejected.append(f"BUY {ticker}: trimmed to the remaining 30-day turnover allowance")
                usd = room
        held_value = pos["shares"] * pos.get("lastPrice", pos["avgCost"]) if pos else 0.0
        usd = min(usd, cap * nav - held_value, state["cash"] - MIN_CASH_FRACTION * nav)
        plan, why_not = edge.evaluate(cfg, price, d.get("target"), d.get("stop"), d.get("prob"), edge.kind_of(bucket), usd)
        if not plan:
            rejected.append(f"BUY {ticker}: odds check failed - {why_not}")
            continue
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
            pos["plan"] = plan
        else:
            state["positions"][ticker] = {"shares": units, "avgCost": cost / units, "lastPrice": quote, "bucket": bucket, "openedDate": date_local, "plan": plan}
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
            "plan": plan,
        }
        if sentiment:
            trade["sentimentSummary"] = sentiment
        executed.append(trade)
        if bucket == "daytrade":
            dt_opens += 1
        else:
            swing_count += 1
    return executed, rejected
