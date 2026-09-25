"""Trade journal, scorecard and standing lessons.

The model itself never changes between sessions, so "learning" here is memory:
- every closed (sold) lot is written to a journal, with the reasons it was bought and sold;
- the next session is asked to review unreviewed entries (was the reasoning sound, or was
  the result luck?) and may keep a short list of standing lessons;
- a scorecard (win rate, average win/loss, by bucket, vs benchmark) goes into every briefing.

Nothing here changes the trading rules or the referee's limits.
"""

MAX_LESSONS = 10
MAX_LESSON_CHARS = 220
VERDICTS = ("sound", "flawed", "lucky", "unlucky")


def _money(cur, v):
    return f"{'-' if v < 0 else '+'}{cur}{abs(v):,.2f}"


def _days_between(a, b):
    from datetime import datetime

    try:
        return (datetime.fromisoformat(b[:10]) - datetime.fromisoformat(a[:10])).days
    except (ValueError, TypeError):
        return None


def journal_entry(sell, trades):
    """Build a journal entry for a sell trade from the trade log (buy context looked up there)."""
    buys = [t for t in trades if t["ticker"] == sell["ticker"] and t["action"] == "buy" and t.get("date", "") <= sell["date"]]
    buy = max(buys, key=lambda t: t["date"]) if buys else None
    pct = sell.get("realizedPnLPct") or 0.0
    entry_px = sell["price"] / (1 + pct / 100) if pct > -100 else None
    return {
        "id": sell["id"],
        "date": sell["date"],
        "ticker": sell["ticker"],
        "bucket": sell.get("bucket"),
        "entryPrice": round(entry_px, 4) if entry_px else None,
        "exitPrice": sell["price"],
        "pnl": sell.get("realizedPnL"),
        "pnlPct": pct,
        "daysHeld": _days_between(buy["date"], sell["date"]) if buy else None,
        "whyBought": (buy or {}).get("rationale", "")[:300],
        "horizon": (buy or {}).get("horizon", ""),
        "whySold": sell.get("rationale", "")[:300],
        "prob": ((buy or {}).get("plan") or {}).get("prob"),
        "rr": ((buy or {}).get("plan") or {}).get("rr"),
        "exitReason": sell.get("exitReason"),
        "review": None,
    }


def ensure(data):
    """Make sure the data file has a journal (backfilled from past sells) and a lessons list."""
    data.setdefault("lessons", [])
    if "journal" not in data:
        sells = sorted((t for t in data["trades"] if t["action"] == "sell" and t.get("realizedPnLPct") is not None), key=lambda t: t["date"])
        data["journal"] = [journal_entry(s, data["trades"]) for s in sells]
    return data


def record_sells(data, executed):
    """Add journal entries for the sells executed this session."""
    known = {j["id"] for j in data["journal"]}
    for t in executed:
        if t["action"] == "sell" and t.get("realizedPnLPct") is not None and t["id"] not in known:
            data["journal"].append(journal_entry(t, data["trades"] + executed))


def apply_reflection(data, decision):
    """Take optional `reviews` and `lessons` from the model's decision and store them (validated)."""
    by_id = {j["id"]: j for j in data["journal"]}
    n_reviews = 0
    for r in decision.get("reviews") or []:
        j = by_id.get(r.get("id")) if isinstance(r, dict) else None
        verdict = str(r.get("verdict", "")).lower() if isinstance(r, dict) else ""
        if j and j["review"] is None and verdict in VERDICTS:
            j["review"] = {"verdict": verdict, "lesson": str(r.get("lesson", ""))[:MAX_LESSON_CHARS]}
            n_reviews += 1
    lessons = decision.get("lessons")
    if isinstance(lessons, list):
        data["lessons"] = [str(x).strip()[:MAX_LESSON_CHARS] for x in lessons if str(x).strip()][:MAX_LESSONS]
    return n_reviews


def _stats(entries):
    n = len(entries)
    if not n:
        return None
    wins = [e["pnlPct"] for e in entries if e["pnlPct"] > 0]
    losses = [e["pnlPct"] for e in entries if e["pnlPct"] <= 0]
    return {
        "n": n,
        "win_rate": len(wins) / n * 100,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "pnl": sum(e["pnl"] or 0 for e in entries),
        "avg_pct": sum(e["pnlPct"] for e in entries) / n,
    }


CAL_BANDS = ((0.0, 0.4), (0.4, 0.55), (0.55, 0.7), (0.7, 1.01))


def calibration(journal):
    """[(band_label, n, avg_predicted, actual_win_rate)] for closed trades that had a probability."""
    rows = []
    for lo, hi in CAL_BANDS:
        es = [e for e in journal if e.get("prob") is not None and lo <= e["prob"] < hi]
        if es:
            rows.append((f"{lo:.0%}-{min(hi, 1):.0%}", len(es), sum(e["prob"] for e in es) / len(es),
                         sum(1 for e in es if e["pnlPct"] > 0) / len(es)))
    return rows


def calibration_text(journal):
    rows = calibration(journal)
    if not rows:
        return ""
    parts = [f"{band}: {n} trade{'s' if n != 1 else ''}, you said {pred:.0%} on average, {act:.0%} actually won" for band, n, pred, act in rows]
    stops = sum(1 for e in journal if e.get("exitReason") == "stop")
    extra = f" Stops hit: {stops}." if stops else ""
    return "  Calibration of your probabilities (win = closed at a profit): " + "; ".join(parts) + "." + extra


def scorecard_text(data, cfg, nav, bench_now=None):
    j = data["journal"]
    state = data["state"]
    cur = cfg["currency"]
    lines = ["SCORECARD (your own track record - facts, not advice):"]
    hist = sorted(data.get("nav_history", []), key=lambda h: h["date"])
    if hist:
        bot = (nav / state["startingCash"] - 1) * 100
        last = hist[-1]
        bnav = last.get("benchmarkNav")
        if bnav:
            bench = (bnav / state["startingCash"] - 1) * 100
            lines.append(f"  Since start: you {bot:+.2f}% vs {cfg['benchmark_name']} {bench:+.2f}% (as of {last['date']}).")
    allst = _stats(j)
    if not allst:
        lines.append("  No closed trades yet - nothing to evaluate. Do not invent lessons from an empty record.")
    else:
        lines.append(f"  Closed trades: {allst['n']}, win rate {allst['win_rate']:.0f}%, avg win {allst['avg_win']:+.2f}%, avg loss {allst['avg_loss']:+.2f}%, total realized {_money(cur, allst['pnl'])}.")
        for b in ("growth", "core", "daytrade"):
            st = _stats([e for e in j if e.get("bucket") == b])
            if st:
                lines.append(f"    {b}: {st['n']} closed, win rate {st['win_rate']:.0f}%, avg {st['avg_pct']:+.2f}% per trade, realized {_money(cur, st['pnl'])}.")
        reviewed = [e for e in j if e["review"]]
        if reviewed:
            counts = {v: sum(1 for e in reviewed if e["review"]["verdict"] == v) for v in VERDICTS}
            lines.append("  Your own past reviews: " + ", ".join(f"{v} {c}" for v, c in counts.items() if c) + ".")
        cal = calibration_text(j)
        if cal:
            lines.append(cal)
        if allst["n"] < 15:
            lines.append(f"  Only {allst['n']} closed trade{'s' if allst['n'] != 1 else ''} so far: too few to draw conclusions. Record observations, but do not change your approach because of a handful of results.")
    pos = state["positions"].values()
    up = sum(1 for p in pos if p.get("lastPrice", p["avgCost"]) >= p["avgCost"])
    lines.append(f"  Open positions: {len(state['positions'])} ({up} above cost).")
    return "\n".join(lines)


def briefing_text(data, cfg, nav):
    """Scorecard + standing lessons + trades awaiting review, for the session briefing."""
    j = data["journal"]
    out = ["", scorecard_text(data, cfg, nav)]
    if data.get("lessons"):
        out += ["", "STANDING LESSONS you wrote for yourself earlier (keep, edit or drop them in your reply):"]
        out += [f"  {i + 1}. {x}" for i, x in enumerate(data["lessons"])]
    pending = [e for e in j if e["review"] is None][-5:]
    if pending:
        out += ["", "CLOSED TRADES AWAITING YOUR REVIEW (add a `reviews` entry for each in your reply):"]
        for e in pending:
            out.append(
                f"  id={e['id']} | {e['ticker']} [{e.get('bucket')}] {e['pnlPct']:+.2f}% ({_money(cfg['currency'], e['pnl'] or 0)}), held {e.get('daysHeld')}d | "
                f"bought because: {e['whyBought'][:200]} | horizon: {e['horizon'][:80]} | sold because: {e['whySold'][:200]}"
            )
    return "\n".join(out)
