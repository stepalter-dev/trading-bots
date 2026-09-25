"""Quarterly review against the success criteria written down in docs/data/fund.json before the period began.

    python tools/review.py            print the review
    python tools/review.py --post     also post it to Discord (DISCORD_WEBHOOK) and save docs/data/review.json

For each market slice, from the rules baseline:
  - return after costs vs the benchmark's return (benchmarkNav tracks an equal-money benchmark holding)
  - worst drawdown of the slice vs the benchmark over the same session history
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bot import learning, notify, store, vc  # noqa: E402
from bot.markets import MARKETS  # noqa: E402


def drawdown(series):
    peak, worst = None, 0.0
    for v in series:
        peak = v if peak is None else max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst * 100


def review_market(key, base):
    d = learning.ensure(store.load(key))
    hist = [h for h in sorted(d["nav_history"], key=lambda x: x["date"]) if h["date"] >= base["date"]]
    if len(hist) < 2:
        return {"key": key, "label": MARKETS[key]["label"], "status": "not enough history yet"}
    last = hist[-1]
    ret = (last["nav"] / base["nav"] - 1) * 100
    bret = (last["benchmarkNav"] / base["benchmarkNav"] - 1) * 100
    dd, bdd = drawdown([base["nav"]] + [h["nav"] for h in hist]), drawdown([base["benchmarkNav"]] + [h["benchmarkNav"] for h in hist])
    since = [t for t in d["trades"] if t["date"][:10] >= base["date"]]
    fees = sum(t.get("fee", 0) + abs(t["price"] - t.get("refPrice", t["price"])) * t["shares"] for t in since)
    beat, safer = ret > bret, dd >= bdd
    return {"key": key, "label": MARKETS[key]["label"], "from": base["date"], "to": last["date"], "return": round(ret, 2), "benchmark": round(bret, 2),
            "drawdown": round(dd, 2), "benchmarkDrawdown": round(bdd, 2), "trades": len(since), "costs": round(fees, 2),
            "beatBenchmark": beat, "shallowerDrawdown": safer,
            "verdict": "PASS" if beat and safer else ("FAIL" if not beat and not safer else "MIXED")}


def main():
    fund = json.load(open(os.path.join(ROOT, "docs", "data", "fund.json"), encoding="utf-8"))
    base = fund["rulesBaseline"]
    rows = [review_market(k, base[k]) for k in ("us", "asx", "crypto")]
    v = vc.load()
    now = datetime.now(timezone.utc)
    lines = [f"Quarterly review - {now:%d %b %Y} (rules adopted {fund['rules']['adopted']})", ""]
    for r in rows:
        if "verdict" not in r:
            lines.append(f"{r['label']}: {r['status']}")
            continue
        lines.append(f"{r['label']}: {r['verdict']} - return {r['return']:+.2f}% vs benchmark {r['benchmark']:+.2f}%; "
                     f"worst drawdown {r['drawdown']:.2f}% vs {r['benchmarkDrawdown']:.2f}%; {r['trades']} trades, costs {r['costs']:,.2f}")
    lines.append(f"VC Scout: NAV A${vc.nav_of(v):,.2f}; {vc.scorecard(v)}")
    lines += ["", "Pre-agreed next step: " + fund["review"]["if_it_fails"]]
    text = "\n".join(lines)
    print(text)
    if "--post" in sys.argv:
        out = {"date": now.strftime("%Y-%m-%d"), "markets": rows, "text": text}
        path = os.path.join(ROOT, "docs", "data", "review.json")
        try:
            prev = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            prev = {"reviews": []}
        prev["reviews"] = [x for x in prev["reviews"] if x["date"] != out["date"]] + [out]
        json.dump(prev, open(path, "w", encoding="utf-8"), indent=1)
        color = 3066993 if all(r.get("verdict") == "PASS" for r in rows) else 15105570
        notify.post({"username": "Fund Review"}, [{"title": "\U0001F4CB Quarterly Fund Review", "description": text[:4000], "color": color,
                                                  "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "footer": {"text": "Judged against criteria set on 2026-09-25"}}])


if __name__ == "__main__":
    main()
