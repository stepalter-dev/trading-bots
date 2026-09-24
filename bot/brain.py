"""The decision-maker: asks Claude (with web search) what to trade this session."""
import json
import os
import re

from .markets import bucket_of, universe

MODEL = os.environ.get("BOT_MODEL", "claude-sonnet-5")
MAX_SEARCHES = int(os.environ.get("BOT_MAX_SEARCHES", "5"))

SYSTEM = """You are the decision engine of a fake-money paper-trading bot competing against a friend's OpenAI-based bot. Nothing here touches real money. You run a "barbell" strategy on the {label} market: a GROWTH bucket (higher-risk) and a CORE bucket (defensive) held for weeks to months, plus a separate DAY-TRADE sleeve (opened and closed within one trading day).

You may ONLY trade tickers from the watchlist supplied below. A deterministic referee applies your proposed trades and will reject or shrink anything that breaks the hard rules, so do not try to bend them:
- At most {max_swing} swing trades (buys/sells of growth/core) and {max_dt} new day-trade opens per session.
- No swing position above 20% of NAV, no day-trade position above 10% of NAV, and at least 5% of NAV always kept in cash.
- Whole shares only for stocks; fractional units are allowed for crypto. No leverage, options, futures or shorting.
- Aim to keep growth vs core (excluding day-trades) inside a 35%-65% band each. Only rebalance for a genuine catalyst.
- Trade only on a specific, real catalyst you found (news, earnings, guidance, sentiment shift, unusual volume). Holding is a good and common outcome; never force a trade because a session fired.
- Every BUY needs an explicit `horizon` tied to a concrete trigger. Review held positions against the horizon recorded when they were bought.
- Day-trades: base them on short-term catalysts (same-day news, technical level, unusual volume ratio), not multi-week theses.
- Unusual volume: `vol_x` is today's volume divided by the recent 10-day average; well above 1.5-2x is a meaningful signal.

Use web search sparingly (a handful of queries) on the names that matter: big movers, held positions, and any candidate you are seriously considering. Prefer recent, specific news over generic commentary. Never invent prices; use only the prices in the data provided.

When you are done, reply with ONE json code block and nothing after it, in exactly this shape:
```json
{{
  "notes": "2-4 sentence plain-English summary of what you looked at and decided, including the growth/core balance",
  "trades": [
    {{"action": "buy", "ticker": "XXX", "bucket": "growth|core|daytrade", "usd": 2500, "rationale": "one or two sentences", "sentiment": "optional one line on social/news sentiment", "horizon": "concrete trigger or date to reassess"}},
    {{"action": "sell", "ticker": "YYY", "shares": "all", "rationale": "..."}}
  ]
}}
```
Use an empty "trades" list when holding. Use "usd" (dollar amount to spend) for buys and "shares" (a number or "all") for sells.

LEARNING FROM YOUR OWN TRACK RECORD. The briefing may end with a SCORECARD, STANDING LESSONS and CLOSED TRADES AWAITING YOUR REVIEW. You have no memory between sessions apart from this; use it honestly:
- For each closed trade awaiting review, add an entry to an optional "reviews" list: {{"id": "<id from the briefing>", "verdict": "sound|flawed|lucky|unlucky", "lesson": "one sentence"}}. Judge the DECISION, not the outcome: "sound" = good reasoning (win or loss); "flawed" = the reasoning was weak or ignored a warning sign; "lucky" = won despite weak reasoning; "unlucky" = good reasoning, bad result.
- Optionally add "lessons": the COMPLETE list (max 10 short strings) of standing lessons you want to keep for future sessions. Only add a lesson supported by at least 3 reviewed trades or a clear repeated pattern, and drop lessons the record no longer supports. Omit "lessons" to leave the list unchanged. With few closed trades, record reviews but do not change your approach.
- Never treat a small sample as proof, and never chase past winners or avoid past losers just because of the last result.
Both "reviews" and "lessons" are optional additions to the same json block."""


def _fmt(x, nd=2):
    return "-" if x is None else f"{x:.{nd}f}"


def build_context(state, cfg, prices, trades, nav, bench, slot, is_last, now_local):
    lines = []
    lines.append(f"Time: {now_local.strftime('%A %Y-%m-%d %H:%M %Z')} (session slot {slot}:00 local; {'LAST session of the day - day-trades will be force-closed and no new day-trades are allowed' if is_last else 'not the last session of the day'})")
    lines.append(f"Currency: {cfg['currency']}   NAV: {nav:,.2f}   Cash: {state['cash']:,.2f} ({state['cash'] / nav * 100:.0f}% of NAV)   Starting capital: {state['startingCash']:,.2f}")
    if bench:
        lines.append(f"Benchmark {cfg['benchmark_name']}: {bench['price']:.2f} ({_fmt(bench.get('chg'))}% today)")
    c = cfg.get("costs")
    if c:
        fee_bits = []
        if c.get("fee_per_unit"):
            fee_bits.append(f"{c['fee_per_unit']} per unit")
        if c.get("fee_pct"):
            fee_bits.append(f"{c['fee_pct']}% of trade value")
        lines.append(f"TRADING COSTS (charged on every fill): slippage ~{c['slip_bps'] / 100:.2f}% against you + fee ({' + '.join(fee_bits)}, minimum {cfg['currency']}{c['fee_min']:g}). "
                     f"A round trip costs roughly {(2 * c['slip_bps'] / 100) + 2 * c.get('fee_pct', 0):.2f}%+ of the position, so small or churny trades lose money by default - only trade when the expected move clearly clears that.")
    lines.append("")
    lines.append("HELD POSITIONS:")
    if not state["positions"]:
        lines.append("  (none - fully in cash)")
    for t, p in state["positions"].items():
        last = p.get("lastPrice", p["avgCost"])
        pnl_pct = (last - p["avgCost"]) / p["avgCost"] * 100 if p["avgCost"] else 0
        origin = next((x for x in sorted(trades, key=lambda z: z.get("date", ""), reverse=True) if x["ticker"] == t and x["action"] == "buy"), None)
        horizon = origin.get("horizon", "n/a") if origin else "n/a"
        reason = (origin.get("rationale", "")[:160] if origin else "")
        lines.append(f"  {t} [{p.get('bucket')}] {p['shares']} @ avg {p['avgCost']:.4f}, now {last:.4f} ({pnl_pct:+.1f}%), value {p['shares'] * last:,.2f} ({p['shares'] * last / nav * 100:.1f}% of NAV) | horizon: {horizon} | why bought: {reason}")
    lines.append("")
    lines.append("WATCHLIST PRICES (ticker | bucket | price | day% | 5d% | vol_x):")
    for t in universe(cfg):
        r = prices.get(t, {})
        if not r.get("ok"):
            lines.append(f"  {t} | {bucket_of(cfg, t)} | UNAVAILABLE THIS SESSION")
            continue
        lines.append(f"  {t} | {bucket_of(cfg, t)} | {r['price']:.4f} | {_fmt(r.get('chg'))} | {_fmt(r.get('chg5'))} | {_fmt(r.get('volratio'))}")
    lines.append("")
    recent = sorted(trades, key=lambda z: z.get("date", ""), reverse=True)[:6]
    if recent:
        lines.append("RECENT TRADES:")
        for x in recent:
            lines.append(f"  {x['date'][:16]} {x['action'].upper()} {x['shares']} {x['ticker']} @ {x['price']}")
        lines.append("")
    if state.get("lastRunNotes"):
        lines.append("Your notes from the previous session: " + state["lastRunNotes"][:600])
    return "\n".join(lines)


def _extract_json(text):
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.S)
    candidates = blocks or re.findall(r"(\{.*\})", text, flags=re.S)
    for c in reversed(candidates):
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise ValueError("no valid JSON decision found in model reply")


def decide(cfg, context):
    """Return {"notes": str, "trades": [...]}. Raises on unrecoverable failure."""
    import anthropic  # imported lazily so offline tests don't need the package

    client = anthropic.Anthropic()
    system = SYSTEM.format(label=cfg["label"], max_swing=3, max_dt=2)
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}]
    messages = [{"role": "user", "content": context}]

    def run(tool_list):
        msgs = list(messages)
        for _ in range(6):  # continue through pause_turn hand-backs
            kwargs = dict(model=MODEL, max_tokens=4000, system=system, messages=msgs)
            if tool_list:
                kwargs["tools"] = tool_list
            resp = client.messages.create(**kwargs)
            if resp.stop_reason == "pause_turn":
                msgs = msgs + [{"role": "assistant", "content": resp.content}]
                continue
            return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        raise RuntimeError("model kept pausing without finishing")

    try:
        text = run(tools)
    except anthropic.BadRequestError as e:
        print("web search unavailable, retrying without it:", e)
        text = run(None)
    decision = _extract_json(text)
    decision.setdefault("trades", [])
    decision.setdefault("notes", "")
    return decision
