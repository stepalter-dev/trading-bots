"""Static definitions for the three paper-trading markets."""

US_GROWTH = ["GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO", "ADBE", "CRM", "AMD", "NFLX", "ORCL", "QQQ", "XLK"]
US_CORE = ["AAPL", "MSFT", "JPM", "V", "UNH", "XOM", "LLY", "COST", "HD", "PG", "KO", "PEP", "INTC", "DIS", "SPY", "VTI", "XLF", "XLE"]

ASX_GROWTH = [t + ".AX" for t in ["XRO", "WTC", "PME", "REA", "CPU", "ALL", "FMG", "NXT", "CAR", "TNE", "GMG", "MIN", "PLS"]]
ASX_CORE = [t + ".AX" for t in ["CBA", "WBC", "NAB", "ANZ", "MQG", "WES", "WOW", "COL", "TLS", "BHP", "RIO", "STO", "WDS", "TCL", "SUN", "QBE", "IAG", "ORG"]]

CRYPTO_GROWTH = ["SOL-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "DOGE-USD", "DOT-USD", "ATOM-USD", "NEAR-USD", "UNI7083-USD", "AAVE-USD"]
CRYPTO_CORE = ["BTC-USD", "ETH-USD", "BNB-USD", "XRP-USD", "LTC-USD", "BCH-USD", "TRX-USD", "XLM-USD"]

MARKETS = {
    "us": {
        "key": "us",
        "label": "US stocks",
        "tz": "America/New_York",
        "currency": "$",
        "benchmark": "SPY",
        "benchmark_name": "S&P 500 (SPY)",
        # Local-clock hours at which a run is allowed to do real work. The
        # last one is the end-of-day session where day-trades are flattened.
        "slots": [10, 12, 14, 15],
        "last_slot": 15,
        "weekdays_only": True,
        "stock_hours": True,  # skip when the benchmark quote is stale (closed / holiday)
        "whole_units": True,
        "username": "Trading Bot Sentinel",
        "prefix": "",
        "growth": US_GROWTH,
        "core": US_CORE,
    },
    "asx": {
        "key": "asx",
        "label": "ASX stocks",
        "tz": "Australia/Sydney",
        "currency": "A$",
        "benchmark": "STW.AX",
        "benchmark_name": "S&P/ASX 200 (STW)",
        "slots": [10, 12, 14, 15],
        "last_slot": 15,
        "weekdays_only": True,
        "stock_hours": True,
        "whole_units": True,
        "username": "ASX Trading Bot",
        "prefix": "[ASX] ",
        "growth": ASX_GROWTH,
        "core": ASX_CORE,
    },
    "crypto": {
        "key": "crypto",
        "label": "crypto",
        "tz": "UTC",
        "currency": "$",
        "benchmark": "BTC-USD",
        "benchmark_name": "Bitcoin (BTC)",
        "slots": [2, 8, 14, 20],
        "last_slot": 20,
        "weekdays_only": False,
        "stock_hours": False,
        "whole_units": False,
        "username": "Crypto Trading Bot",
        "prefix": "[CRYPTO] ",
        "growth": CRYPTO_GROWTH,
        "core": CRYPTO_CORE,
    },
}


def bucket_of(cfg, ticker):
    if ticker in cfg["growth"]:
        return "growth"
    if ticker in cfg["core"]:
        return "core"
    return None


def universe(cfg):
    return cfg["growth"] + cfg["core"]
