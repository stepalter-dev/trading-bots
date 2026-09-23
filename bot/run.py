"""Entry point: python -m bot.run <us|asx|crypto> [--force] [--dry] [--stub]

--force  ignore the slot/weekday/duplicate/market-open gates (for manual tests)
--dry    do everything but do not save data or post to Discord
--stub   skip the Claude call and just hold (offline tests)
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import brain, engine, notify, prices as pricing, store
from .markets import MARKETS, universe

DUPLICATE_MINUTES = 45


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("market", choices=sorted(MARKETS))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--stub", action="store_true")
    args = ap.parse_args(argv)

    cfg = MARKETS[args.market]
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(ZoneInfo(cfg["tz"]))
    date_local = now_local.strftime("%Y-%m-%d")
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    data = store.load(cfg["key"])
    state, nav_history, trades = data["state"], data["nav_history"], data["trades"]

    # ---- gates: is this a real session? -------------------------------------
    if not args.force:
        if cfg["weekdays_only"] and now_local.weekday() >= 5:
            print(f"[{cfg['key']}] weekend in {cfg['tz']} - nothing to do")
            return 0
        if now_local.hour not in cfg["slots"]:
            print(f"[{cfg['key']}] local hour {now_local.hour} is not a session slot {cfg['slots']} - nothing to do")
            return 0
        last = state.get("lastUpdated")
        if last:
            age = now_utc - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if age < timedelta(minutes=DUPLICATE_MINUTES):
                print(f"[{cfg['key']}] last run only {age.seconds // 60} min ago - duplicate guard, skipping")
                return 0

    # ---- prices -------------------------------------------------------------
    tickers = list(dict.fromkeys(universe(cfg) + [cfg["benchmark"]]))
    prices = pricing.fetch_all(tickers)
    failed = [t for t, r in prices.items() if not r["ok"]]
    feed_failed = len(failed) / len(prices) > 0.5
    print(f"[{cfg['key']}] fetched {len(prices) - len(failed)}/{len(prices)} prices; failed: {failed}")

    bench = prices[cfg["benchmark"]] if prices[cfg["benchmark"]]["ok"] else None
    if cfg["stock_hours"] and not args.force:
        qt = bench.get("quote_time") if bench else None
        if not qt or now_utc.timestamp() - qt > 45 * 60:
            print(f"[{cfg['key']}] benchmark quote is stale - market closed or holiday, nothing to do")
            return 0

    slot = now_local.hour
    is_last = slot == cfg["last_slot"]
    engine.mark_to_market(state, prices)

    # ---- trading ------------------------------------------------------------
    executed, notes = [], []
    flat, fnotes = engine.forced_flatten(state, cfg, prices, now_iso, date_local, is_last)
    executed += flat
    notes += fnotes

    decision_notes = ""
    if feed_failed:
        decision_notes = "Price feed unhealthy this session (more than half of price fetches failed) - no new trades, holdings marked at last-known prices."
    else:
        nav = engine.nav_of(state)
        context = brain.build_context(state, cfg, prices, trades, nav, bench, slot, is_last, now_local)
        try:
            decision = {"notes": "Stub run - holding.", "trades": []} if args.stub else brain.decide(cfg, context)
        except Exception as e:  # noqa: BLE001 - a brain failure must never corrupt state
            print("Decision step failed:", repr(e))
            decision = {"notes": f"Decision step failed this session ({type(e).__name__}) - holding; no trades.", "trades": []}
        decision_notes = decision.get("notes", "")
        done, rejected = engine.apply_decisions(state, cfg, prices, decision.get("trades", []), now_iso, date_local, is_last)
        executed += done
        if rejected:
            notes.append("Referee adjusted/rejected: " + "; ".join(rejected))

    # ---- accounting ---------------------------------------------------------
    nav = engine.nav_of(state)
    bv = engine.bucket_values(state)
    swing_total = bv["growth"] + bv["core"]
    growth_pct = bv["growth"] / swing_total * 100 if swing_total > 0 else None

    hist = sorted(nav_history, key=lambda h: h["date"])
    last_h = hist[-1] if hist else None
    if bench and last_h and last_h.get("benchmarkClose"):
        bench_nav = last_h["benchmarkNav"] * bench["price"] / last_h["benchmarkClose"]
    elif last_h:
        bench_nav = last_h["benchmarkNav"]
    else:
        bench_nav = state["startingCash"]
    bench_close = bench["price"] if bench else (last_h or {}).get("benchmarkClose")

    entry = {
        "date": date_local,
        "nav": round(nav, 2),
        "cash": round(state["cash"], 2),
        "positionsValue": round(nav - state["cash"], 2),
        "benchmarkNav": round(bench_nav, 2),
        "benchmarkClose": bench_close,
        "growthValue": round(bv["growth"], 2),
        "coreValue": round(bv["core"], 2),
        "daytradeValue": round(bv["daytrade"], 2),
    }
    nav_history[:] = [h for h in nav_history if h["date"] != date_local] + [entry]

    state["dataFeedFailures"] = state.get("dataFeedFailures", 0) + 1 if feed_failed else 0
    state["dataFeedAlert"] = (
        f"{state['dataFeedFailures']} consecutive sessions with a failing price feed - trading paused until it recovers."
        if state["dataFeedFailures"] >= 3 else None
    )
    state["lastUpdated"] = now_iso
    state["lastRunNotes"] = " ".join(x for x in [decision_notes] + notes if x).strip()
    trades.extend(executed)

    local_time = f"{now_local:%I:%M%p}".lstrip("0") + f" {now_local:%Z}"
    print(f"[{cfg['key']}] NAV {nav:,.2f} | cash {state['cash']:,.2f} | trades {len(executed)} | {state['lastRunNotes'][:200]}")

    if args.dry:
        print("--dry: not saving, not posting")
        return 0

    store.save(cfg["key"], data)
    embeds = notify.build_embeds(
        cfg, nav=nav, bench_nav=bench_nav, growth_pct=growth_pct, feed_alert=state["dataFeedAlert"],
        feed_failed_now=feed_failed, executed=executed, notes=state["lastRunNotes"],
        now_utc=now_utc, local_time=local_time, start_cash=state["startingCash"],
    )
    notify.post(cfg, embeds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
