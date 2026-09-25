"""Asymmetric-odds check: every buy must come with a plan (target, stop, probability).

The referee computes reward:risk and expected value after trading costs, and rejects
buys that do not clear the minimums. Held positions carry their plan; a price at or
below the stop is sold automatically (the idea was proven wrong), and a price at or
above the target is flagged for the model to take profit or raise the plan.

The probability is the model's own estimate, so the journal tracks calibration
(were its 60% calls right about 60% of the time?).
"""
from .engine import costs_of, fee_of, fill_price

MIN_RR = {"swing": 2.0, "daytrade": 1.5}
MIN_EV_PCT = 0.0  # expected value after round-trip costs must be positive
MAX_STOP_PCT = {"swing": 35.0, "daytrade": 10.0}  # a stop further than this is not a real stop


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN guard


def parse_prob(x):
    """Accept 0.6, "60", "60%"; return a fraction in (0, 1) or None."""
    if isinstance(x, str):
        x = x.strip().rstrip("%")
    p = _num(x)
    if p is None:
        return None
    if p > 1:
        p /= 100.0
    return p if 0 < p < 1 else None


def roundtrip_cost_pct(cfg, price, usd):
    """Slippage both ways plus buy and sell fees, as % of the position."""
    if price <= 0 or usd <= 0:
        return 0.0
    buy = fill_price(cfg, price, "buy")
    units = usd / buy
    sell = fill_price(cfg, price, "sell")
    fees = fee_of(cfg, units, buy) + fee_of(cfg, units, sell)
    return (buy - sell) / price * 100 + fees / usd * 100


def evaluate(cfg, price, target, stop, prob, kind, usd):
    """Return (plan, None) if the trade clears the bar, else (None, reason)."""
    target, stop, p = _num(target), _num(stop), parse_prob(prob)
    if target is None or stop is None or p is None:
        return None, "needs a numeric target, stop and probability (0-100%)"
    if target <= price:
        return None, f"target {target:g} is not above the price {price:g}"
    if not 0 < stop < price:
        return None, f"stop {stop:g} must be below the price {price:g}"
    up = (target - price) / price * 100
    down = (price - stop) / price * 100
    if down > MAX_STOP_PCT[kind]:
        return None, f"stop is {down:.1f}% away (max {MAX_STOP_PCT[kind]:.0f}% for {kind})"
    rr = up / down
    cost = roundtrip_cost_pct(cfg, price, usd or 1000.0)
    ev = p * up - (1 - p) * down - cost
    plan = {"target": round(target, 6), "stop": round(stop, 6), "prob": round(p, 3),
            "rr": round(rr, 2), "evPct": round(ev, 2), "entryRef": price}
    if rr < MIN_RR[kind]:
        return None, f"reward:risk {rr:.2f} is below the {MIN_RR[kind]:.1f} minimum for {kind} (up {up:.1f}% / down {down:.1f}%)"
    if ev <= MIN_EV_PCT:
        return None, f"expected value {ev:+.2f}% after {cost:.2f}% costs is not positive (p={p:.0%}, up {up:.1f}%, down {down:.1f}%)"
    return plan, None


def kind_of(bucket):
    return "daytrade" if bucket == "daytrade" else "swing"


def set_plans(state, cfg, prices, plans):
    """Attach or replace plans on held positions without trading. Returns notes."""
    notes = []
    for d in plans or []:
        if not isinstance(d, dict):
            continue
        t = d.get("ticker")
        pos = state["positions"].get(t)
        r = prices.get(t) or {}
        if not pos or not r.get("ok"):
            notes.append(f"PLAN {t}: not held or no price")
            continue
        target, stop, p = _num(d.get("target")), _num(d.get("stop")), parse_prob(d.get("prob"))
        price = r["price"]
        if target is None or stop is None or p is None or not (0 < stop < price < target):
            notes.append(f"PLAN {t}: needs stop < price ({price:g}) < target and a probability")
            continue
        up, down = (target - price) / price * 100, (price - stop) / price * 100
        pos["plan"] = {"target": round(target, 6), "stop": round(stop, 6), "prob": round(p, 3),
                       "rr": round(up / down, 2), "evPct": round(p * up - (1 - p) * down, 2), "entryRef": price, "setLater": True}
    return notes


def stop_exits(state, cfg, prices, now_iso, date_local, sell_fn):
    """Sell any position whose price is at or below its plan's stop. Returns (trades, notes)."""
    trades, notes = [], []
    for ticker, pos in list(state["positions"].items()):
        plan = pos.get("plan")
        r = prices.get(ticker) or {}
        if not plan or not r.get("ok") or r["price"] > plan["stop"]:
            continue
        why = f"Stop hit: price {r['price']:g} is at/below the planned stop {plan['stop']:g} - the thesis was proven wrong, exiting per plan."
        t = sell_fn(state, cfg, ticker, pos["shares"], r["price"], now_iso, date_local, why, None)
        if t:
            t["exitReason"] = "stop"
            trades.append(t)
    return trades, notes


def plan_line(pos, price, cur):
    plan = pos.get("plan")
    if not plan:
        return "NO PLAN - set one via `plans` (target, stop, probability)"
    to_t = (plan["target"] - price) / price * 100
    to_s = (price - plan["stop"]) / price * 100
    flag = "  ** TARGET REACHED - take profit or set a new plan **" if price >= plan["target"] else ""
    return (f"plan: target {cur}{plan['target']:g} ({to_t:+.1f}% away), stop {cur}{plan['stop']:g} ({to_s:.1f}% below), "
            f"your odds {plan['prob']:.0%}, R:R {plan['rr']:.1f}{flag}")
