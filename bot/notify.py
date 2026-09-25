"""Discord notifications and next-run countdown."""
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .http import post_json

RED, GREEN, BLUE, ORANGE = 15158332, 3066993, 3447003, 15105570


def next_slot_epoch(cfg, now_utc):
    tz = ZoneInfo(cfg["tz"])
    now_local = now_utc.astimezone(tz)
    for day in range(0, 9):
        d = (now_local + timedelta(days=day)).date()
        if cfg["weekdays_only"] and d.weekday() >= 5:
            continue
        for h in sorted(cfg["slots"]):
            cand = datetime(d.year, d.month, d.day, h, 10, tzinfo=tz)
            if cand > now_local:
                return int(cand.timestamp())
    return None


def _money(cfg, x):
    return ("-" if x < 0 else "") + cfg["currency"] + f"{abs(x):,.2f}"


def build_embeds(cfg, *, nav, bench_nav, growth_pct, feed_alert, feed_failed_now, executed, notes, now_utc, local_time, start_cash, anchor_pct=None):
    p = cfg["prefix"]
    iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    n = len(executed)
    if feed_alert or feed_failed_now:
        title, color = "\U0001F6A8 Data Feed Issue - Trading Paused", RED
    elif n:
        title, color = f"✅ Session Complete - {n} Trade(s)", GREEN
    else:
        title, color = "\U0001F4CA Session Complete - Holding", BLUE
    delta = nav - bench_nav
    fields = [
        {"name": "Session", "value": local_time, "inline": True},
        {"name": "NAV", "value": f"{_money(cfg, nav)} ({'+' if delta >= 0 else ''}{_money(cfg, delta)} vs {cfg['benchmark_name']})", "inline": True},
    ]
    if anchor_pct is not None:
        fields.append({"name": "Index Anchor", "value": f"{anchor_pct:.0f}% in {cfg['benchmark']}", "inline": True})
    if growth_pct is not None:
        fields.append({"name": "Growth / Core Split", "value": f"{growth_pct:.0f}% / {100 - growth_pct:.0f}%", "inline": True})
    fields.append({"name": "Data Feed", "value": feed_alert or ("Degraded this session - no trades" if feed_failed_now else "Healthy"), "inline": True})
    epoch = next_slot_epoch(cfg, now_utc)
    if epoch:
        fields.append({"name": "Next Update", "value": f"<t:{epoch}:R>", "inline": True})
    embeds = [{
        "title": p + title,
        "description": (notes or "")[:1500],
        "color": color,
        "fields": fields,
        "timestamp": iso,
        "footer": {"text": f"{cfg['label'].title()} bot - paper trading"},
    }]
    for t in executed:
        is_buy = t["action"] == "buy"
        unit = "Units" if not cfg["whole_units"] else "Shares"
        f = [
            {"name": unit, "value": f"`{t['shares']}`", "inline": True},
            {"name": "Price", "value": f"`{_money(cfg, t['price'])}`", "inline": True},
            {"name": "Total Value", "value": f"`{_money(cfg, t['shares'] * t['price'])}`", "inline": True},
        ]
        if t.get("fee") is not None:
            slip = abs(t["price"] - t.get("refPrice", t["price"])) * t["shares"]
            f.append({"name": "Costs", "value": f"`{_money(cfg, t['fee'])}` fee + `{_money(cfg, slip)}` slippage", "inline": True})
        if t.get("realizedPnL") is not None:
            pnl = t["realizedPnL"]
            f.append({"name": "Realized P&L", "value": f"**{'+' if pnl >= 0 else ''}{_money(cfg, pnl)} ({t.get('realizedPnLPct', 0):+.2f}%)**", "inline": True})
        plan = t.get("plan")
        if plan:
            f.append({"name": "Plan", "value": f"Target `{_money(cfg, plan['target'])}` · Stop `{_money(cfg, plan['stop'])}` · Odds `{plan['prob']:.0%}` · R:R `{plan['rr']:.1f}:1`", "inline": False})
        if t.get("exitReason") == "stop":
            f.append({"name": "Exit", "value": "Stop-loss hit - sold per plan", "inline": False})
        f.append({"name": "Rationale", "value": "> *" + (t.get("rationale") or "")[:900] + "*", "inline": False})
        embeds.append({
            "title": p + ("\U0001F535 BUY " if is_buy else "\U0001F7E0 SELL ") + t["ticker"] + (" (day-trade)" if t.get("bucket") == "daytrade" else ""),
            "description": f"Routed to paper ledger under **{(t.get('bucket') or '').upper()}** bucket.",
            "color": BLUE if is_buy else ORANGE,
            "fields": f,
            "timestamp": iso,
        })
    if feed_alert:
        embeds.append({
            "title": p + "\U0001F6A8 Data Feed Circuit Breaker Tripped",
            "description": "**Trading has been paused - no trades were placed.** No positions were closed or liquidated; holdings are marked at last-known prices until the feed recovers.",
            "color": RED,
            "timestamp": iso,
        })
    return embeds[:10]


def post(cfg, embeds):
    url = os.environ.get("DISCORD_WEBHOOK", "").strip()
    if not url:
        print("DISCORD_WEBHOOK not set - skipping Discord post")
        return False
    status = post_json(url, {"username": cfg["username"], "embeds": embeds})
    print("Discord:", status)
    return 200 <= status < 300
