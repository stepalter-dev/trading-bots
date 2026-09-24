"""Live price refresh (no Claude, no trading): writes docs/data/live.json.

    python -m bot.tick

Fetches the latest price of every held ticker (plus each benchmark) and stores them
separately from the bots' own data files, so it never conflicts with a session's save.
The dashboard overlays these prices on the holdings between sessions.
"""
import json
import os
import sys
from datetime import datetime, timezone

from . import prices as pricing, store
from .markets import MARKETS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "docs", "data", "live.json")


def main():
    try:
        with open(PATH, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    out = {"markets": {}}
    for key, cfg in MARKETS.items():
        held = list(store.load(key)["state"].get("positions", {}))
        tickers = list(dict.fromkeys(held + [cfg["benchmark"]]))
        got = pricing.fetch_all(tickers)
        prev = old.get("markets", {}).get(key, {}).get("prices", {})
        px = {}
        for t in tickers:
            r = got[t]
            if r["ok"]:
                px[t] = round(r["price"], 6)
            elif t in prev:
                px[t] = prev[t]  # keep last known on a failed fetch
        out["markets"][key] = {"prices": px, "benchmark": cfg["benchmark"]}
    if out["markets"] == old.get("markets"):
        print("no price change")
        return 0
    out["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("updated", out["updatedAt"], {k: len(v["prices"]) for k, v in out["markets"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
