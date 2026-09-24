"""Session lifecycle shared by every way of running a bot.

begin()   - gates + prices; returns a session dict, or raises Skip
finish()  - apply a decision, do the accounting, save, post to Discord
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import engine, learning, notify, prices as pricing, store
from .markets import MARKETS, universe

DUPLICATE_MINUTES = 45


class Skip(Exception):
    """Not a real session right now (wrong hour, weekend, closed market, duplicate)."""


def begin(key, force=False, now_utc=None):
    cfg = MARKETS[key]
    now_utc = now_utc or datetime.now(timezone.utc)
    now_local = now_utc.astimezone(ZoneInfo(cfg["tz"]))
    data = learning.ensure(store.load(key))
    state = data["state"]

    if not force:
        if cfg["weekdays_only"] and now_local.weekday() >= 5:
            raise Skip(f"weekend in {cfg['tz']}")
        if now_local.hour not in cfg["slots"]:
            raise Skip(f"local hour {now_local.hour} is not a session slot {cfg['slots']}")
        last = state.get("lastUpdated")
        if last:
            age = now_utc - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if age < timedelta(minutes=DUPLICATE_MINUTES):
                raise Skip(f"last run was only {age.seconds // 60} minutes ago (duplicate guard)")

    tickers = list(dict.fromkeys(universe(cfg) + [cfg["benchmark"]]))
    prices = pricing.fetch_all(tickers)
    failed = [t for t, r in prices.items() if not r["ok"]]
    bench = prices[cfg["benchmark"]] if prices[cfg["benchmark"]]["ok"] else None
    if cfg["stock_hours"] and not force:
        qt = bench.get("quote_time") if bench else None
        if not qt or now_utc.timestamp() - qt > 45 * 60:
            raise Skip("benchmark quote is stale - market closed or holiday")

    return {
        "cfg": cfg,
        "data": data,
        "prices": prices,
        "failed": failed,
        "feed_failed": len(failed) / len(prices) > 0.5,
        "bench": bench,
        "now_utc": now_utc,
        "now_local": now_local,
        "date_local": now_local.strftime("%Y-%m-%d"),
        "now_iso": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "slot": now_local.hour,
        "is_last": now_local.hour == cfg["last_slot"],
    }


def finish(s, decision, dry=False):
    """Apply `decision` ({"notes": str, "trades": [...]}) and persist. Returns a summary string."""
    cfg, data, prices = s["cfg"], s["data"], s["prices"]
    state, nav_history, trades = data["state"], data["nav_history"], data["trades"]
    engine.mark_to_market(state, prices)

    executed, notes = [], []
    flat, fnotes = engine.forced_flatten(state, cfg, prices, s["now_iso"], s["date_local"], s["is_last"])
    executed += flat
    notes += fnotes

    if s["feed_failed"]:
        decision_notes = "Price feed unhealthy this session (more than half of price fetches failed) - no new trades, holdings marked at last-known prices."
    else:
        decision_notes = decision.get("notes", "")
        done, rejected = engine.apply_decisions(state, cfg, prices, decision.get("trades", []), s["now_iso"], s["date_local"], s["is_last"])
        executed += done
        if rejected:
            notes.append("Referee adjusted/rejected: " + "; ".join(rejected))

    nav = engine.nav_of(state)
    bv = engine.bucket_values(state)
    swing_total = bv["growth"] + bv["core"]
    growth_pct = bv["growth"] / swing_total * 100 if swing_total > 0 else None

    hist = sorted(nav_history, key=lambda h: h["date"])
    last_h = hist[-1] if hist else None
    bench = s["bench"]
    if bench and last_h and last_h.get("benchmarkClose"):
        bench_nav = last_h["benchmarkNav"] * bench["price"] / last_h["benchmarkClose"]
    elif last_h:
        bench_nav = last_h["benchmarkNav"]
    else:
        bench_nav = state["startingCash"]
    bench_close = bench["price"] if bench else (last_h or {}).get("benchmarkClose")

    entry = {
        "date": s["date_local"],
        "nav": round(nav, 2),
        "cash": round(state["cash"], 2),
        "positionsValue": round(nav - state["cash"], 2),
        "benchmarkNav": round(bench_nav, 2),
        "benchmarkClose": bench_close,
        "growthValue": round(bv["growth"], 2),
        "coreValue": round(bv["core"], 2),
        "daytradeValue": round(bv["daytrade"], 2),
    }
    nav_history[:] = [h for h in nav_history if h["date"] != s["date_local"]] + [entry]

    failures = state.get("dataFeedFailures", 0) + 1 if s["feed_failed"] else 0
    state["dataFeedFailures"] = failures
    state["dataFeedAlert"] = f"{failures} consecutive sessions with a failing price feed - trading paused until it recovers." if failures >= 3 else None
    state["lastUpdated"] = s["now_iso"]
    state["lastRunNotes"] = " ".join(x for x in [decision_notes] + notes if x).strip()
    trades.extend(executed)
    learning.record_sells(data, executed)
    learning.apply_reflection(data, decision)

    now_local = s["now_local"]
    local_time = f"{now_local:%I:%M%p}".lstrip("0") + f" {now_local:%Z}"
    summary = f"[{cfg['key']}] NAV {nav:,.2f} | cash {state['cash']:,.2f} | trades {len(executed)} | {state['lastRunNotes'][:240]}"
    if dry:
        return summary + "\n--dry: not saved, not posted"

    store.save(cfg["key"], data)
    embeds = notify.build_embeds(
        cfg, nav=nav, bench_nav=bench_nav, growth_pct=growth_pct, feed_alert=state["dataFeedAlert"],
        feed_failed_now=s["feed_failed"], executed=executed, notes=state["lastRunNotes"],
        now_utc=s["now_utc"], local_time=local_time, start_cash=state["startingCash"],
    )
    notify.post(cfg, embeds)
    return summary
