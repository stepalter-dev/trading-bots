"""Price/volume/trend data from Yahoo Finance's public chart endpoint (2 years of daily bars)."""
import time
from concurrent.futures import ThreadPoolExecutor

from .http import get_json

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2y"
DAY = 86400


def close_on_or_before(ts, closes, when):
    """Last close at or before unix time `when` (ts/closes are aligned, ascending)."""
    best = None
    for t, c in zip(ts, closes):
        if t > when:
            break
        best = c
    return best


def trend_stats(ts, closes, now_ts):
    """200-day moving average and 12-1 month momentum (return from 12 months ago to 1 month ago)."""
    ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
    p1 = close_on_or_before(ts, closes, now_ts - 30 * DAY)
    p12 = close_on_or_before(ts, closes, now_ts - 365 * DAY)
    mom = (p1 / p12 - 1) * 100 if p1 and p12 and ts and ts[0] <= now_ts - 360 * DAY else None
    return ma200, mom


def parse_chart(res):
    meta = res["meta"]
    price = meta.get("regularMarketPrice")
    if price is None:
        raise RuntimeError("no price")
    quote = res.get("indicators", {}).get("quote", [{}])[0]
    raw_ts = res.get("timestamp") or []
    pairs = [(t, c) for t, c in zip(raw_ts, quote.get("close") or []) if c is not None]
    ts = [t for t, _ in pairs]
    closes = [c for _, c in pairs]
    vols = [v for v in (quote.get("volume") or []) if v is not None]
    # previous session's close: the bar before the latest one (NOT chartPreviousClose, which is the
    # close before the whole chart range)
    prev = closes[-2] if len(closes) >= 2 else None
    chg = ((price - prev) / prev * 100) if prev else None
    chg5 = ((price - closes[-6]) / closes[-6] * 100) if len(closes) >= 6 else None
    volratio = None
    if len(vols) >= 6 and vols[-1] and sum(vols[-11:-1]):
        base = vols[-11:-1]
        volratio = vols[-1] / (sum(base) / len(base))
    now_ts = ts[-1] if ts else time.time()
    ma200, mom = trend_stats(ts, closes, now_ts)
    return {
        "ok": True,
        "price": float(price),
        "prev": prev,
        "chg": chg,
        "chg5": chg5,
        "volratio": volratio,
        "quote_time": meta.get("regularMarketTime"),
        "ma200": ma200,
        "above_ma200": (price > ma200) if ma200 else None,
        "mom12_1": mom,
    }


def fetch_one(ticker, retries=2):
    err = "unknown"
    for attempt in range(retries + 1):
        try:
            return parse_chart(get_json(URL.format(ticker=ticker))["chart"]["result"][0])
        except Exception as e:  # noqa: BLE001 - any failure counts as a failed fetch
            err = str(e)
            time.sleep(0.6 * (attempt + 1))
    return {"ok": False, "error": err}


def fetch_all(tickers):
    """Return {ticker: result}. Runs requests concurrently."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_one, tickers))
    return dict(zip(tickers, results))
