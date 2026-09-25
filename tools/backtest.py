"""Backtest the rule-based filters on up to 10 years of daily prices (monthly rebalancing, with costs).

    python tools/backtest.py            -> prints a table and writes BACKTEST.md

Strategies per market (the watchlist is today's, so results carry survivorship bias - compare the
strategies with each other more than with the benchmark):
  bench        buy and hold the benchmark
  ew           equal weight across the whole watchlist
  ew_trend     equal weight, only names above their 200-day average (the rest in cash)
  ew_filter    equal weight, only names above their 200-day average AND with positive 12-1 momentum
  mom5         the 5 names with the best 12-1 month momentum
  mom5_trend   the 5 best by momentum among names above their 200-day average (else cash)
  core60_*     60% benchmark + 40% in the strategy named after it
"""
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bot.http import get_json  # noqa: E402
from bot.markets import MARKETS, universe  # noqa: E402

DAY = 86400


def history(ticker):
    try:
        r = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=10y")["chart"]["result"][0]
        closes = r["indicators"]["quote"][0]["close"]
        out = {}
        for t, c in zip(r["timestamp"], closes):
            if c:
                out[t // DAY] = c  # key by UTC day number
        return ticker, out
    except Exception:  # noqa: BLE001
        return ticker, {}


def cost_pct(cfg):
    c = cfg.get("costs") or {}
    return (c.get("slip_bps", 0) / 100 + c.get("fee_pct", 0) + (0.05 if c.get("fee_per_unit") else 0)) / 100  # per side, as a fraction


def run(key):
    cfg = MARKETS[key]
    bench = cfg["benchmark"]
    names = [t for t in universe(cfg) if t != bench]
    with ThreadPoolExecutor(max_workers=8) as pool:
        data = dict(pool.map(history, names + [bench]))
    bdays = sorted(data[bench])
    if len(bdays) < 300:
        return None
    # month-start rebalance days, after one year of warm-up for the 12-month signals
    rebal = [d for i, d in enumerate(bdays) if i > 260 and (i == 0 or (d * DAY // (30.44 * DAY)) != (bdays[i - 1] * DAY // (30.44 * DAY)))]

    def px(t, d):
        s = data[t]
        return s.get(d)

    def last_on_or_before(t, d, lookback=10):
        for k in range(lookback):
            v = data[t].get(d - k)
            if v:
                return v
        return None

    def ma200(t, d):
        days = [x for x in sorted(data[t]) if x <= d][-200:]
        return sum(data[t][x] for x in days) / 200 if len(days) == 200 else None

    def mom(t, d):
        p1, p12 = last_on_or_before(t, d - 30), last_on_or_before(t, d - 365)
        return p1 / p12 - 1 if p1 and p12 else None

    def weights(strategy, d):
        if strategy == "bench":
            return {bench: 1.0}
        live = [t for t in names if last_on_or_before(t, d) and mom(t, d) is not None]
        if not live and not strategy.startswith("core60_"):
            return {}
        if strategy == "ew":
            return {t: 1 / len(live) for t in live}
        up = [t for t in live if (m := ma200(t, d)) and last_on_or_before(t, d) > m]
        if strategy == "ew_trend":
            return {t: 1 / len(live) for t in up}  # names below trend -> their share stays in cash
        if strategy == "ew_filter":  # the live rule: above the 200-day average AND positive 12-1 momentum
            ok = [t for t in up if mom(t, d) > 0]
            return {t: 1 / len(live) for t in ok}
        if strategy == "mom5":
            top = sorted(live, key=lambda t: -mom(t, d))[:5]
            return {t: 1 / 5 for t in top}
        if strategy == "mom5_trend":
            top = sorted(up, key=lambda t: -mom(t, d))[:5]
            return {t: 1 / 5 for t in top}
        if strategy.startswith("core60_"):
            w = {t: 0.4 * v for t, v in weights(strategy[7:], d).items()}
            w[bench] = w.get(bench, 0) + 0.6
            return w
        raise ValueError(strategy)

    per_side = cost_pct(cfg)
    results = {}
    for strat in ("bench", "ew", "ew_trend", "ew_filter", "mom5", "mom5_trend", "core60_ew", "core60_ew_filter", "core60_mom5_trend"):
        nav, hold, curve, costs = 1.0, {}, [], 0.0
        for i, d in enumerate(rebal):
            # mark to market since the last rebalance
            if hold:
                prev = rebal[i - 1]
                hold = {t: (v if t == "_cash" else v * last_on_or_before(t, d) / last_on_or_before(t, prev)) for t, v in hold.items()}
                nav = sum(hold.values())  # holdings drift with prices between rebalances
            w = weights(strat, d)
            target = {t: nav * v for t, v in w.items()}
            target["_cash"] = nav - sum(target.values())
            turnover = sum(abs(target.get(t, 0) - hold.get(t, 0)) for t in set(target) | set(hold) if t != "_cash")
            c = turnover * per_side
            costs += c
            nav -= c
            hold = {t: v * (nav / (nav + c)) for t, v in target.items()}
            curve.append((d, nav))
        years = (curve[-1][0] - curve[0][0]) / 365.25
        cagr = curve[-1][1] ** (1 / years) - 1
        peak, mdd = 0, 0
        for _, v in curve:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        rets = [curve[i][1] / curve[i - 1][1] - 1 for i in range(1, len(curve))]
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        results[strat] = {"cagr": cagr, "mdd": mdd, "vol": sd * math.sqrt(12), "sharpe": (mu * 12) / (sd * math.sqrt(12)) if sd else 0,
                          "costs": costs, "years": years}
    return results


def main():
    lines = ["# Backtest of the rule-based filters", "",
             "Monthly rebalancing on up to 10 years of daily closes, costs included. The watchlists are today's, so every "
             "stock strategy benefits from survivorship bias: compare strategies with each other more than with the benchmark.", ""]
    for key in ("us", "asx", "crypto"):
        res = run(key)
        if not res:
            continue
        yrs = next(iter(res.values()))["years"]
        lines += [f"## {MARKETS[key]['label']} (benchmark {MARKETS[key]['benchmark']}, {yrs:.1f} years)", "",
                  "| strategy | CAGR | max drawdown | volatility | Sharpe | total costs (x start capital) |", "|---|---|---|---|---|---|"]
        for s, r in res.items():
            lines.append(f"| {s} | {r['cagr']:+.1%} | {r['mdd']:.1%} | {r['vol']:.1%} | {r['sharpe']:.2f} | {r['costs']:.1%} |")
        lines.append("")
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(ROOT, "BACKTEST.md"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
