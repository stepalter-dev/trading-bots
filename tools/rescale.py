"""One-off (2026-09-25): move from separate ~45k portfolios to one A$40,000 fund in fixed slices.

Each market becomes a A$10,000 slice (converted to US$ for US stocks and crypto at the given
AUD/USD rate). Every money amount and share count is scaled by the same factor, so all
percentage returns, prices and the chart shape stay exactly the same.

    python tools/rescale.py 0.7025 [us asx crypto]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bot import engine, store  # noqa: E402

SLICE_AUD = 10000.0
USD_MARKETS = {"us", "crypto"}

STATE_MONEY = ("cash",)
NAV_MONEY = ("nav", "cash", "positionsValue", "benchmarkNav", "growthValue", "coreValue", "daytradeValue")
TRADE_MONEY = ("fee", "realizedPnL")
JOURNAL_MONEY = ("pnl",)


def _scale(obj, keys, f):
    for k in keys:
        if isinstance(obj.get(k), (int, float)):
            obj[k] = round(obj[k] * f, 6)


def rescale(data, target_start):
    s = data["state"]
    f = target_start / s["startingCash"]
    nav_before = engine.nav_of(s)
    _scale(s, STATE_MONEY, f)
    for p in s["positions"].values():
        p["shares"] = round(p["shares"] * f, 8)  # legacy holdings may now be fractional
    s["startingCash"] = round(target_start, 2)
    for h in data.get("nav_history", []):
        _scale(h, NAV_MONEY, f)
    for t in data.get("trades", []):
        t["shares"] = round(t["shares"] * f, 8)
        _scale(t, TRADE_MONEY, f)
    for j in data.get("journal", []):
        _scale(j, JOURNAL_MONEY, f)
    s["rescaled"] = {"factor": round(f, 8), "sliceAud": SLICE_AUD, "note": "Scaled from a separate portfolio into an A$10,000 slice of one A$40,000 fund on 2026-09-25."}
    nav_after = engine.nav_of(s)
    assert abs(nav_after / s["startingCash"] - nav_before / (target_start / f)) < 1e-5, "return changed"
    return f


def main():
    audusd = float(sys.argv[1])
    for key in sys.argv[2:] or ("us", "asx", "crypto"):
        data = store.load(key)
        target = SLICE_AUD * audusd if key in USD_MARKETS else SLICE_AUD
        before = engine.nav_of(data["state"]) / data["state"]["startingCash"] - 1
        f = rescale(data, target)
        after = engine.nav_of(data["state"]) / data["state"]["startingCash"] - 1
        store.save(key, data)
        print(f"{key}: start {data['state']['startingCash']:,.2f}  factor {f:.5f}  return {before:+.4%} -> {after:+.4%}")


if __name__ == "__main__":
    main()
