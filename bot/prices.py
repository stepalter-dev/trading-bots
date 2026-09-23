"""Price/volume fetching from Yahoo Finance's public chart endpoint."""
import time
from concurrent.futures import ThreadPoolExecutor

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (paper-trading-bot)"}
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1mo"


def fetch_one(ticker, retries=2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(URL.format(ticker=ticker), headers=HEADERS, timeout=15)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            res = r.json()["chart"]["result"][0]
            meta = res["meta"]
            price = meta.get("regularMarketPrice")
            if price is None:
                raise RuntimeError("no price")
            quote = res.get("indicators", {}).get("quote", [{}])[0]
            closes = [c for c in (quote.get("close") or []) if c is not None]
            vols = [v for v in (quote.get("volume") or []) if v is not None]
            prev = meta.get("chartPreviousClose") or (closes[-2] if len(closes) >= 2 else None)
            chg = ((price - prev) / prev * 100) if prev else None
            chg5 = ((price - closes[-6]) / closes[-6] * 100) if len(closes) >= 6 else None
            volratio = None
            if len(vols) >= 6 and vols[-1] and sum(vols[-11:-1]):
                base = vols[-11:-1]
                volratio = vols[-1] / (sum(base) / len(base))
            return {
                "ok": True,
                "price": float(price),
                "prev": prev,
                "chg": chg,
                "chg5": chg5,
                "volratio": volratio,
                "quote_time": meta.get("regularMarketTime"),
            }
        except Exception as e:  # noqa: BLE001 - any failure counts as a failed fetch
            err = str(e)
            time.sleep(0.6 * (attempt + 1))
    return {"ok": False, "error": err}


def fetch_all(tickers):
    """Return {ticker: result}. Runs requests concurrently."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_one, tickers))
    return dict(zip(tickers, results))
