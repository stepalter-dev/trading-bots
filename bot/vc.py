"""VC Scout: a paper venture fund (A$10,000 slice) that backs early-stage startups.

Private companies have no share price, so this is a forecasting experiment with fake cheques:
- each week the scout sees fresh candidates from the YC directory, Show HN, Product Hunt and
  fast-growing new GitHub repos, researches them and writes 0-5 small cheques;
- every cheque records an assumed entry valuation and two forecasts (raises new money within
  12 months; still operating after 12 months) that are scored when they resolve;
- holdings are marked like a real VC fund: at cost until a verified event (new round, acquisition,
  shutdown), with an evidence link required for every mark.

    python -m bot.session prepare vc [--force]
    python -m bot.session apply vc decision.json [--force] [--dry] [--no-push]
"""
import json
import os
import random
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import unescape

from . import notify, store

CFG = {
    "key": "vc", "label": "VC Scout", "currency": "A$", "tz": "Australia/Sydney",
    "username": "VC Scout", "prefix": "[VC] ", "start": 10000.0, "inception": "2026-09-25",
    "reserve_pct": 0.20,        # kept back for follow-ons
    "deploy_weeks": 52,         # pace new cheques over a year
    "max_new": 5, "min_cheque": 20.0, "budget_flex": 1.5,
    "max_other": 1,             # picks found outside the candidate list, per week
    "followon_mult": 2.0,       # a follow-on can be at most 2x the first cheque
    "min_gap_days": 5,          # weekly cadence; guards duplicate fires
    "check_every_days": 21,     # re-check each holding for news this often
    "raise_step_up": 2.5,       # assumed mark-up when a round is announced without a valuation
}
EVENTS = ("raised", "down_round", "acquired", "shutdown", "ipo")
UA = {"User-Agent": "paper-trading-bot research contact: windsorkerrj@gmail.com"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAND_FILE = os.path.join(ROOT, "_export", "vc_candidates.json")

SYSTEM = """You are the VC Scout: the partner of a small fake-money venture fund (A${start:,.0f}, part of a paper-trading experiment; nothing here is real money). Your job is to find early-stage startups before they are obvious, write small cheques, and be honestly calibrated about which ones will make it.

How the fund works (a referee enforces these):
- This session you may make up to {max_new} new investments. The cheques together should stay within this week's budget (shown in the briefing); the referee trims anything above {flex:g}x the budget and keeps a {reserve:.0%} reserve for follow-ons. Minimum cheque A${min_cheque:g}. Picking fewer (even zero) is fine when nothing is good.
- Prefer companies from the CANDIDATES list. At most {max_other} pick per week may come from your own research (any accelerator, e.g. Techstars, 500 Global, Plug and Play, university incubators), and it needs a source URL.
- Every investment needs: an assumed post-money valuation in US$ at which your cheque goes in (be realistic for the stage: pre-seed/YC-stage usually US$10-30M, seed US$15-40M, Series A US$40-120M), a short thesis, and two probabilities (0-100): that it announces new funding within 12 months, and that it is still operating in 12 months. These are scored later, so be honest; most early startups fail.
- Marks: holdings stay at cost until a verified event. Report events you found for holdings in "updates" with an evidence URL (news article, company post, or the YC directory). Events: raised (new round; give the post-money valuation if disclosed), down_round (valuation required), acquired (give the price/valuation if disclosed), shutdown, ipo. Never mark on hype or traction alone.
- Follow-ons: only into a holding with a verified raise in the last 60 days, at the new round's valuation, at most {fmult:g}x your first cheque.
- List holdings you checked with no news in "checked".

Research: use web search on the candidates you are considering (what they do, founders, traction, competition, any funding news) and on the holdings due for a check. Think like a seed investor: team, market size, early traction signals (GitHub stars, Show HN/Product Hunt reception, hiring), and why now.

Reply with ONE json code block and nothing after it:
```json
{{
  "notes": "2-4 sentences: what you looked at, what you backed and why, anything notable in the portfolio",
  "investments": [
    {{"id": "<candidate id, or a short slug for your own find>", "name": "Company", "source_url": "https://... (required if not from the list)", "cheque": 60, "valuation_usd": 20000000, "p_raise_12m": 35, "p_alive_12m": 70, "thesis": "2-3 sentences"}}
  ],
  "followons": [{{"id": "<holding id>", "amount": 50, "valuation_usd": 60000000}}],
  "updates": [{{"id": "<holding id>", "event": "raised", "valuation_usd": 60000000, "evidence": "https://...", "note": "one line"}}],
  "checked": ["<holding id>"]
}}
```
Empty lists are fine."""


# ------------------------------------------------------------------ data

def blank():
    return {"state": {"cash": CFG["start"], "startingCash": CFG["start"], "holdings": {}, "seen": [],
                      "lastUpdated": None, "lastRunNotes": ""},
            "nav_history": [], "deals": [], "forecasts": []}


def load():
    try:
        return store.load("vc")
    except FileNotFoundError:
        return blank()


def holding_value(h):
    if h["status"] in ("dead", "exited"):
        return 0.0
    return sum(t["amount"] * h["markValuation"] / t["valuation"] for t in h["tranches"])


def invested(h):
    return sum(t["amount"] for t in h["tranches"])


def nav_of(data):
    s = data["state"]
    return s["cash"] + sum(holding_value(h) for h in s["holdings"].values())


def weekly_budget(data, today):
    s = data["state"]
    weeks = (today - date.fromisoformat(CFG["inception"])).days // 7
    weeks_left = max(4, CFG["deploy_weeks"] - weeks)
    deployable = max(0.0, s["cash"] - CFG["reserve_pct"] * s["startingCash"])
    return deployable / weeks_left, deployable


# ------------------------------------------------------------------ candidate sources

def _get(url, as_json=True):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        body = r.read().decode("utf-8", errors="replace")
    return json.loads(body) if as_json else body


_SEASON = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}


def _batch_key(slug):
    m = re.match(r"(winter|spring|summer|fall)-(\d{4})$", slug)
    return (int(m.group(2)), _SEASON[m.group(1)]) if m else (0, 0)


def yc_candidates(seen, rng, n=12):
    meta = _get("https://yc-oss.github.io/api/meta.json")
    batches = sorted((b for b in meta["batches"] if _batch_key(b)[0]), key=_batch_key, reverse=True)
    pool, used = [], 0
    for b in batches[:4]:  # newest batches first; early lists are small, so keep going until there is a decent pool
        if len(pool) >= 60 and used >= 2:
            break
        used += 1
        for c in _get(meta["batches"][b]["api"]):
            cid = "yc-" + c["slug"]
            if cid in seen or c.get("status") != "Active":
                continue
            pool.append({"id": cid, "source": "YC " + c.get("batch", ""), "name": c["name"], "oneLiner": c.get("one_liner", ""),
                         "url": c.get("url"), "website": c.get("website"), "teamSize": c.get("team_size"),
                         "tags": c.get("tags"), "ycApi": c.get("api"), "hiring": c.get("isHiring")})
    rng.shuffle(pool)
    return pool[:n]


def hn_candidates(seen, now, n=8):
    since = int((now - timedelta(days=7)).timestamp())
    d = _get(f"https://hn.algolia.com/api/v1/search?tags=show_hn&numericFilters=created_at_i>{since},points>=100&hitsPerPage=40")
    out = []
    for h in sorted(d["hits"], key=lambda x: -x.get("points", 0)):
        cid = "hn-" + h["objectID"]
        if cid in seen:
            continue
        out.append({"id": cid, "source": "Show HN", "name": re.sub(r"^Show HN:\s*", "", h["title"]), "oneLiner": "",
                    "url": f"https://news.ycombinator.com/item?id={h['objectID']}", "website": h.get("url"),
                    "points": h.get("points"), "comments": h.get("num_comments")})
    return out[:n]


def ph_candidates(seen, n=8):
    feed = _get("https://www.producthunt.com/feed", as_json=False)
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", feed, flags=re.S):
        pid = re.search(r"Post/(\d+)", e)
        title = re.search(r"<title>(.*?)</title>", e, flags=re.S)
        link = re.search(r'<link rel="alternate" type="text/html" href="([^"]+)"', e)
        content = re.search(r"<content[^>]*>(.*?)</content>", e, flags=re.S)
        if not (pid and title):
            continue
        cid = "ph-" + pid.group(1)
        if cid in seen:
            continue
        tagline = ""
        if content:
            ps = re.findall(r"<p>(.*?)</p>", unescape(content.group(1)), flags=re.S)
            tagline = re.sub(r"<[^>]+>", "", ps[0]).strip() if ps else ""
        out.append({"id": cid, "source": "Product Hunt", "name": unescape(title.group(1)).strip(), "oneLiner": tagline[:200],
                    "url": link.group(1) if link else None, "website": None})
    return out[:n]


BIG_ORGS = {"google", "google-deepmind", "microsoft", "meta", "facebook", "facebookresearch", "openai", "anthropics", "deepseek-ai",
            "alibaba", "qwenlm", "tencent", "bytedance", "nvidia", "apple", "amazon", "aws", "awslabs", "huggingface", "baidu",
            "xai-org", "mistralai", "ibm", "oracle", "salesforce", "adobe", "vercel", "cloudflare"}


def gh_candidates(seen, today, n=8):
    since = (today - timedelta(days=45)).isoformat()
    d = _get(f"https://api.github.com/search/repositories?q=created:%3E{since}+fork:false&sort=stars&order=desc&per_page=30")
    out = []
    for r in d.get("items", []):
        cid = "gh-" + r["full_name"].lower()
        owner, name = r["full_name"].lower().split("/", 1)
        if cid in seen or owner in BIG_ORGS or name.startswith("awesome"):
            continue  # big-company projects and curated lists are not startups
        out.append({"id": cid, "source": "GitHub", "name": r["full_name"], "oneLiner": (r.get("description") or "")[:200],
                    "url": r["html_url"], "website": r.get("homepage") or None, "stars": r.get("stargazers_count"),
                    "created": r.get("created_at", "")[:10], "ownerType": r.get("owner", {}).get("type")})
    return out[:n]


def gather_candidates(data, now):
    seen = set(data["state"].get("seen", [])) | set(data["state"]["holdings"])
    rng = random.Random(now.strftime("%Y-%m-%d"))
    out, errors = [], []
    for name, fn in (("YC", lambda: yc_candidates(seen, rng)), ("Show HN", lambda: hn_candidates(seen, now)),
                     ("Product Hunt", lambda: ph_candidates(seen)), ("GitHub", lambda: gh_candidates(seen, now.date()))):
        try:
            out += fn()
        except Exception as e:  # noqa: BLE001 - one broken source must not stop the session
            errors.append(f"{name}: {type(e).__name__}")
    return out, errors


# ------------------------------------------------------------------ forecasts / scoring

def resolve_forecasts(data, today):
    """Resolve forecasts whose outcome is now known. Returns the number resolved."""
    n = 0
    hs = data["state"]["holdings"]
    for f in data["forecasts"]:
        if f.get("outcome") is not None:
            continue
        h = hs.get(f["id"], {})
        due = date.fromisoformat(f["due"])
        if f["event"] == "raise_12m":
            raised = h.get("firstRaiseDate")
            if raised and raised <= f["due"]:
                f["outcome"], f["resolved"] = True, raised
            elif h.get("status") == "dead" or today > due:
                f["outcome"], f["resolved"] = False, today.isoformat()
        elif f["event"] == "alive_12m":
            if h.get("status") == "dead":
                f["outcome"], f["resolved"] = False, h.get("deadDate", today.isoformat())
            elif today > due:
                f["outcome"], f["resolved"] = True, today.isoformat()
        n += f.get("outcome") is not None
    return n


def scorecard(data):
    res = [f for f in data["forecasts"] if f.get("outcome") is not None]
    open_ = len(data["forecasts"]) - len(res)
    if not res:
        return f"No forecasts resolved yet ({open_} open). Most resolve 12 months after each cheque, or earlier on a raise or shutdown."
    brier = sum((f["prob"] - (1.0 if f["outcome"] else 0.0)) ** 2 for f in res) / len(res)
    lines = [f"Resolved forecasts: {len(res)} ({open_} open). Brier score {brier:.3f} (0 = perfect, 0.25 = coin-flip guessing)."]
    for ev in ("raise_12m", "alive_12m"):
        fs = [f for f in res if f["event"] == ev]
        if fs:
            pred = sum(f["prob"] for f in fs) / len(fs)
            act = sum(1 for f in fs if f["outcome"]) / len(fs)
            lines.append(f"  {ev}: you said {pred:.0%} on average, {act:.0%} happened ({len(fs)} resolved).")
    return "\n".join(lines)


# ------------------------------------------------------------------ prepare

class Skip(Exception):
    pass


def begin(force=False, now=None):
    now = now or datetime.now(timezone.utc)
    data = load()
    last = data["state"].get("lastUpdated")
    if last and not force:
        age = now - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if age < timedelta(days=CFG["min_gap_days"]):
            raise Skip(f"last VC session was {age.days} days ago (weekly cadence)")
    return data, now


def due_for_check(data, today):
    out = []
    for h in data["state"]["holdings"].values():
        if h["status"] in ("dead", "exited"):
            continue
        last = h.get("lastChecked") or h["investedDate"]
        if (today - date.fromisoformat(last[:10])).days >= CFG["check_every_days"]:
            out.append(h)
    return sorted(out, key=lambda h: h.get("lastChecked") or "")[:10]


def _yc_status(h):
    if not h.get("ycApi"):
        return None
    try:
        return _get(h["ycApi"]).get("status")
    except Exception:  # noqa: BLE001
        return None


def briefing(data, now, cands, errors):
    s = data["state"]
    today = now.date()
    budget, deployable = weekly_budget(data, today)
    nav = nav_of(data)
    L = [f"Date: {today.isoformat()} (weekly session)",
         f"Fund NAV A${nav:,.2f} (start A${s['startingCash']:,.0f}) | cash A${s['cash']:,.2f} | deployable after reserve A${deployable:,.2f}",
         f"THIS WEEK'S BUDGET for new cheques: about A${budget:,.2f} in total (referee cap A${budget * CFG['budget_flex']:,.2f}).",
         "", "PORTFOLIO:"]
    live = [h for h in s["holdings"].values()]
    if not live:
        L.append("  (no holdings yet - this is an early session)")
    for h in sorted(live, key=lambda x: x["investedDate"]):
        mult = holding_value(h) / invested(h) if invested(h) else 0
        L.append(f"  id={h['id']} {h['name']} [{h['source']}] {h['status']} | in A${invested(h):,.2f} at US${h['entryValuation'] / 1e6:,.0f}M "
                 f"-> marked US${h['markValuation'] / 1e6:,.0f}M ({mult:.2f}x) | invested {h['investedDate']} | last checked {h.get('lastChecked') or 'never'}")
    due = due_for_check(data, today)
    if due:
        L += ["", "HOLDINGS DUE FOR A NEWS CHECK (search for funding, acquisition, shutdown; report in updates/checked):"]
        for h in due:
            yc = _yc_status(h)
            L.append(f"  id={h['id']} {h['name']} - {h.get('website') or h.get('url')}" + (f" | YC directory status now: {yc}" if yc else ""))
    L += ["", "SCORECARD: " + scorecard(data)]
    L += ["", f"CANDIDATES ({len(cands)} new this week; ids are what you use in 'investments'):"]
    for c in cands:
        extra = []
        for k, label in (("teamSize", "team"), ("points", "HN points"), ("comments", "HN comments"), ("stars", "GitHub stars"), ("created", "repo created")):
            if c.get(k):
                extra.append(f"{label} {c[k]}")
        if c.get("hiring") == "True":
            extra.append("hiring")
        L.append(f"  id={c['id']} | {c['source']} | {c['name']} - {c.get('oneLiner') or ''} | {c.get('website') or c.get('url')}"
                 + (f" | {', '.join(extra)}" if extra else ""))
    if errors:
        L.append("  (sources unavailable this week: " + ", ".join(errors) + ")")
    return "\n".join(L)


def prepare(force=False):
    try:
        data, now = begin(force)
    except Skip as why:
        print(f"SKIP: {why}. Nothing to do this fire - stop here.")
        return 0
    cands, errors = gather_candidates(data, now)
    os.makedirs(os.path.dirname(CAND_FILE), exist_ok=True)
    with open(CAND_FILE, "w", encoding="utf-8") as f:
        json.dump(cands, f)
    print(SYSTEM.format(start=CFG["start"], max_new=CFG["max_new"], flex=CFG["budget_flex"], reserve=CFG["reserve_pct"],
                        min_cheque=CFG["min_cheque"], max_other=CFG["max_other"], fmult=CFG["followon_mult"]))
    print("\n========== BRIEFING ==========")
    print(briefing(data, now, cands, errors))
    print("\n========== YOUR TASK ==========")
    print("Research with web search, decide, save ONLY the JSON object to a file (e.g. /tmp/decision.json) and run the `apply` command.")
    return 0


# ------------------------------------------------------------------ apply

def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _prob(x):
    p = _num(str(x).rstrip("%") if isinstance(x, str) else x)
    if p is None:
        return None
    p = p / 100 if p > 1 else p
    return p if 0 < p < 1 else None


def _url(x):
    return isinstance(x, str) and re.match(r"https?://\S+\.\S+", x.strip()) is not None


def apply_decision(data, decision, now, cands):
    """Validate and apply. Returns (new_deals, notes)."""
    s, today = data["state"], now.date()
    iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    by_id = {c["id"]: c for c in cands}
    deals, notes = [], []
    hs = s["holdings"]

    # 1) updates / marks first (they can enable follow-ons)
    for u in decision.get("updates") or []:
        h = hs.get(u.get("id")) if isinstance(u, dict) else None
        ev = str((u or {}).get("event", "")).lower() if isinstance(u, dict) else ""
        if not h or ev not in EVENTS or h["status"] in ("dead", "exited"):
            notes.append(f"update {u.get('id') if isinstance(u, dict) else u}: unknown holding/event")
            continue
        if not _url(u.get("evidence")):
            notes.append(f"update {h['id']}: needs an evidence URL")
            continue
        val = _num(u.get("valuation_usd"))
        before = holding_value(h)
        assumed = False
        if ev == "raised":
            if not val:
                val, assumed = h["markValuation"] * CFG["raise_step_up"], True
            h["markValuation"] = val
            h["lastRaiseDate"] = today.isoformat()
            h.setdefault("firstRaiseDate", today.isoformat())
        elif ev == "down_round":
            if not val or val >= h["markValuation"]:
                notes.append(f"update {h['id']}: a down round needs a lower valuation")
                continue
            h["markValuation"] = val
            h["lastRaiseDate"] = today.isoformat()
            h.setdefault("firstRaiseDate", today.isoformat())
        elif ev == "ipo":
            if val:
                h["markValuation"] = val
            h["status"] = "public"
        elif ev == "acquired":
            if val:
                h["markValuation"] = val
            proceeds = holding_value(h)
            s["cash"] += proceeds
            h["status"], h["exitDate"], h["proceeds"] = "exited", today.isoformat(), round(proceeds, 2)
        elif ev == "shutdown":
            h["status"], h["deadDate"] = "dead", today.isoformat()
        h["lastChecked"] = today.isoformat()
        deals.append({"type": ev, "id": h["id"], "name": h["name"], "date": iso, "valuation": h["markValuation"],
                      "valueBefore": round(before, 2), "valueAfter": round(holding_value(h) if h["status"] != "exited" else h.get("proceeds", 0), 2),
                      "assumedMark": assumed, "evidence": u["evidence"].strip(), "note": str(u.get("note", ""))[:300]})

    for cid in decision.get("checked") or []:
        if cid in hs:
            hs[cid]["lastChecked"] = today.isoformat()

    # 2) follow-ons from cash (reserve allowed)
    for f in decision.get("followons") or []:
        h = hs.get(f.get("id")) if isinstance(f, dict) else None
        amt, val = _num((f or {}).get("amount")), _num((f or {}).get("valuation_usd"))
        if not h or not amt or amt <= 0 or not val:
            notes.append("follow-on: needs a holding, amount and valuation")
            continue
        recent = h.get("lastRaiseDate") and (today - date.fromisoformat(h["lastRaiseDate"])).days <= 60
        if not recent or h["status"] in ("dead", "exited"):
            notes.append(f"follow-on {h['id']}: only after a verified raise in the last 60 days")
            continue
        amt = min(amt, CFG["followon_mult"] * h["tranches"][0]["amount"] - sum(t["amount"] for t in h["tranches"][1:]), s["cash"])
        if amt < CFG["min_cheque"]:
            notes.append(f"follow-on {h['id']}: nothing left within the follow-on limit")
            continue
        s["cash"] -= amt
        h["tranches"].append({"amount": round(amt, 2), "valuation": val, "date": today.isoformat()})
        deals.append({"type": "followon", "id": h["id"], "name": h["name"], "date": iso, "amount": round(amt, 2), "valuation": val})

    # 3) new investments within the weekly budget
    budget, deployable = weekly_budget(data, today)
    cap = min(budget * CFG["budget_flex"], deployable)
    spent, n_new, n_other = 0.0, 0, 0
    for d in decision.get("investments") or []:
        if not isinstance(d, dict):
            continue
        cid = str(d.get("id") or "").strip()
        cand = by_id.get(cid)
        if n_new >= CFG["max_new"]:
            notes.append(f"invest {cid}: more than {CFG['max_new']} new cheques this week")
            continue
        if not cand:
            if n_other >= CFG["max_other"] or not _url(d.get("source_url")):
                notes.append(f"invest {cid}: not a listed candidate (own finds need a source_url, max {CFG['max_other']} a week)")
                continue
            cid = "own-" + re.sub(r"[^a-z0-9]+", "-", (d.get("name") or cid).lower()).strip("-")[:40]
            cand = {"id": cid, "source": "Own research", "name": d.get("name") or cid, "url": d["source_url"].strip(), "oneLiner": ""}
        if cid in hs:
            notes.append(f"invest {cid}: already held (use followons)")
            continue
        cheque, val = _num(d.get("cheque")), _num(d.get("valuation_usd"))
        pr, pa = _prob(d.get("p_raise_12m")), _prob(d.get("p_alive_12m"))
        thesis = str(d.get("thesis") or "").strip()
        if not cheque or cheque < CFG["min_cheque"]:
            notes.append(f"invest {cid}: cheque below A${CFG['min_cheque']:g}")
            continue
        if not val or not 1e6 <= val <= 3e8:
            notes.append(f"invest {cid}: valuation must be US$1M-300M")
            continue
        if pr is None or pa is None or not thesis:
            notes.append(f"invest {cid}: needs both probabilities and a thesis")
            continue
        cheque = min(cheque, cap - spent)
        if cheque < CFG["min_cheque"]:
            notes.append(f"invest {cid}: weekly budget used up")
            continue
        s["cash"] -= cheque
        spent += cheque
        n_new += 1
        n_other += cand["source"] == "Own research"
        due = (today + timedelta(days=365)).isoformat()
        hs[cid] = {"id": cid, "name": cand["name"], "source": cand["source"], "url": cand.get("url"), "website": cand.get("website"),
                   "oneLiner": cand.get("oneLiner", ""), "ycApi": cand.get("ycApi"), "teamSizeAtEntry": cand.get("teamSize"),
                   "investedDate": today.isoformat(), "tranches": [{"amount": round(cheque, 2), "valuation": val, "date": today.isoformat()}],
                   "entryValuation": val, "markValuation": val, "status": "active", "thesis": thesis[:600],
                   "pRaise12m": round(pr, 3), "pAlive12m": round(pa, 3), "lastChecked": today.isoformat()}
        data["forecasts"] += [{"id": cid, "event": "raise_12m", "prob": round(pr, 3), "made": today.isoformat(), "due": due, "outcome": None},
                              {"id": cid, "event": "alive_12m", "prob": round(pa, 3), "made": today.isoformat(), "due": due, "outcome": None}]
        deals.append({"type": "invest", "id": cid, "name": cand["name"], "source": cand["source"], "date": iso, "amount": round(cheque, 2),
                      "valuation": val, "pRaise12m": round(pr, 3), "pAlive12m": round(pa, 3), "thesis": thesis[:600], "url": cand.get("url")})

    s["seen"] = sorted(set(s.get("seen", [])) | set(by_id))[-3000:]
    resolve_forecasts(data, today)
    return deals, notes


def embeds_for(data, deals, notes_text, now):
    s = data["state"]
    nav = nav_of(data)
    new = [d for d in deals if d["type"] == "invest"]
    marks = [d for d in deals if d["type"] not in ("invest", "followon")]
    color = 3066993 if new else 3447003
    nxt = now + timedelta(days=7)
    fields = [
        {"name": "Fund NAV", "value": f"`A${nav:,.2f}` ({(nav / s['startingCash'] - 1) * 100:+.2f}%)", "inline": True},
        {"name": "Cash", "value": f"`A${s['cash']:,.2f}`", "inline": True},
        {"name": "Portfolio", "value": f"{sum(1 for h in s['holdings'].values() if h['status'] not in ('dead', 'exited'))} live holdings", "inline": True},
        {"name": "Next Scout", "value": f"<t:{int(nxt.timestamp())}:R>", "inline": True},
    ]
    embeds = [{"title": f"{CFG['prefix']}Weekly Scout - {len(new)} new cheque{'s' if len(new) != 1 else ''}", "description": notes_text[:1500],
               "color": color, "fields": fields, "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "footer": {"text": "VC Scout - paper venture fund"}}]
    for d in new[:6]:
        embeds.append({"title": f"{CFG['prefix']}Backed {d['name']}", "url": d.get("url"), "color": 10181046,
                       "description": f"> *{d['thesis'][:600]}*",
                       "fields": [{"name": "Cheque", "value": f"`A${d['amount']:,.2f}`", "inline": True},
                                  {"name": "Entry valuation", "value": f"`US${d['valuation'] / 1e6:,.0f}M`", "inline": True},
                                  {"name": "Source", "value": d.get("source", "-"), "inline": True},
                                  {"name": "Odds", "value": f"Raises in 12m `{d['pRaise12m']:.0%}` - Alive in 12m `{d['pAlive12m']:.0%}`", "inline": False}]})
    for d in marks[:3]:
        embeds.append({"title": f"{CFG['prefix']}{d['type'].replace('_', ' ').title()}: {d['name']}", "url": d["evidence"], "color": 15158332 if d["type"] == "shutdown" else 3066993,
                       "description": f"Value A${d['valueBefore']:,.2f} -> A${d['valueAfter']:,.2f}" + (" (assumed step-up, valuation undisclosed)" if d.get("assumedMark") else "") + (f"\n{d['note']}" if d.get("note") else "")})
    return embeds


def apply(decision, force=False, dry=False):
    data, now = begin(force)
    try:
        with open(CAND_FILE, encoding="utf-8") as f:
            cands = json.load(f)
    except (OSError, ValueError):
        cands = []
    deals, notes = apply_decision(data, decision, now, cands)
    s = data["state"]
    nav = nav_of(data)
    day = now.date().isoformat()
    entry = {"date": day, "nav": round(nav, 2), "cash": round(s["cash"], 2), "holdingsValue": round(nav - s["cash"], 2),
             "deployed": round(sum(invested(h) for h in s["holdings"].values()), 2)}
    data["nav_history"] = [h for h in data["nav_history"] if h["date"] != day] + [entry]
    data["deals"].extend(deals)
    s["lastUpdated"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    s["lastRunNotes"] = " ".join([str(decision.get("notes", "")).strip()] + (["Referee: " + "; ".join(notes)] if notes else [])).strip()
    summary = f"[vc] NAV A${nav:,.2f} | cash A${s['cash']:,.2f} | deals {len(deals)} | {s['lastRunNotes'][:240]}"
    if dry:
        return summary + "\n--dry: not saved, not posted"
    store.save("vc", data)
    notify.post(CFG, embeds_for(data, deals, s["lastRunNotes"], now))
    return summary
