"""Alternative-data signals for the session briefing (free, public sources, no keys).

- Insider trades (US): SEC EDGAR Form 4 filings - open-market purchases (code P) and sales (S).
- Public attention (all markets): Wikipedia page views, last 7 days vs the prior 28.
- Developer activity (crypto): GitHub commits on each project's main repository.
- Retail crypto interest: Coinbase's rank in the US App Store (Finance, free), tracked over time.

Every source is optional: a failure just drops that line. Signals are context for the model,
never a trade trigger on their own.
"""
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

UA = "paper-trading-bot research contact: windsorkerrj@gmail.com"
TIMEOUT = 12

WIKI = {
    "GOOGL": "Alphabet Inc.", "AMZN": "Amazon (company)", "NVDA": "Nvidia", "META": "Meta Platforms", "TSLA": "Tesla, Inc.",
    "AVGO": "Broadcom", "ADBE": "Adobe Inc.", "CRM": "Salesforce", "AMD": "AMD", "NFLX": "Netflix", "ORCL": "Oracle Corporation",
    "AAPL": "Apple Inc.", "MSFT": "Microsoft", "JPM": "JPMorgan Chase", "V": "Visa Inc.", "UNH": "UnitedHealth Group",
    "XOM": "ExxonMobil", "LLY": "Eli Lilly and Company", "COST": "Costco", "HD": "Home Depot", "PG": "Procter & Gamble",
    "KO": "The Coca-Cola Company", "PEP": "PepsiCo", "INTC": "Intel", "DIS": "The Walt Disney Company",
    "XRO.AX": "Xero", "REA.AX": "REA Group", "CPU.AX": "Computershare", "ALL.AX": "Aristocrat Leisure", "FMG.AX": "Fortescue",
    "NXT.AX": "NextDC", "CAR.AX": "CAR Group", "TNE.AX": "TechnologyOne", "GMG.AX": "Goodman Group", "MIN.AX": "Mineral Resources",
    "PLS.AX": "PLS (company)", "CBA.AX": "Commonwealth Bank", "WBC.AX": "Westpac", "NAB.AX": "National Australia Bank",
    "ANZ.AX": "ANZ (bank)", "MQG.AX": "Macquarie Group", "WES.AX": "Wesfarmers", "WOW.AX": "Woolworths Group", "COL.AX": "Coles Group",
    "TLS.AX": "Telstra", "BHP.AX": "BHP", "RIO.AX": "Rio Tinto", "STO.AX": "Santos Limited", "WDS.AX": "Woodside Energy",
    "TCL.AX": "Transurban", "SUN.AX": "Suncorp Group", "QBE.AX": "QBE Insurance", "IAG.AX": "Insurance Australia Group",
    "ORG.AX": "Origin Energy",
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "BNB-USD": "Binance", "XRP-USD": "XRP Ledger", "LTC-USD": "Litecoin",
    "BCH-USD": "Bitcoin Cash", "TRX-USD": "Tron (blockchain)", "XLM-USD": "Stellar (payment network)",
    "SOL-USD": "Solana (blockchain platform)", "ADA-USD": "Cardano (blockchain platform)", "AVAX-USD": "Avalanche (blockchain platform)",
    "LINK-USD": "Chainlink (blockchain oracle)", "DOGE-USD": "Dogecoin", "DOT-USD": "Polkadot (blockchain platform)",
    "NEAR-USD": "NEAR (blockchain platform)", "UNI7083-USD": "Uniswap", "AAVE-USD": "Aave",
}

REPOS = {
    "BTC-USD": "bitcoin/bitcoin", "ETH-USD": "ethereum/go-ethereum", "BNB-USD": "bnb-chain/bsc@develop", "XRP-USD": "XRPLF/rippled",
    "LTC-USD": "litecoin-project/litecoin", "TRX-USD": "tronprotocol/java-tron", "XLM-USD": "stellar/stellar-core",
    "SOL-USD": "anza-xyz/agave", "ADA-USD": "IntersectMBO/cardano-node", "AVAX-USD": "ava-labs/avalanchego",
    "LINK-USD": "smartcontractkit/chainlink", "DOGE-USD": "dogecoin/dogecoin", "DOT-USD": "paritytech/polkadot-sdk",
    "ATOM-USD": "cosmos/cosmos-sdk", "NEAR-USD": "near/nearcore", "UNI7083-USD": "Uniswap/v4-periphery", "AAVE-USD": "aave-dao/aave-v3-origin",
}


def _get(url, accept_json=True):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json" if accept_json else "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read().decode("utf-8", errors="replace")
        return (json.loads(body) if accept_json else body), r.status


# ---------------------------------------------------------------- attention

def wiki_attention(ticker, today=None):
    """(ratio of last-7-day avg views to prior-28-day avg, last7 avg) or None."""
    title = WIKI.get(ticker)
    if not title:
        return None
    today = today or datetime.now(timezone.utc).date()
    end = today - timedelta(days=1)
    start = end - timedelta(days=34)
    art = urllib.request.quote(title.replace(" ", "_"), safe="")
    url = (f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
           f"{art}/daily/{start:%Y%m%d}/{end:%Y%m%d}")
    items = _get(url)[0].get("items", [])
    views = [i["views"] for i in items]
    if len(views) < 21:
        return None
    last7, prior = views[-7:], views[:-7][-28:]
    base = sum(prior) / len(prior)
    if base <= 0:
        return None
    return sum(last7) / 7 / base, sum(last7) / 7


# ---------------------------------------------------------------- insiders (SEC)

_CIK = {}


def _cik(ticker):
    if not _CIK:
        data, _ = _get("https://www.sec.gov/files/company_tickers.json")
        for row in data.values():
            _CIK[row["ticker"].upper()] = int(row["cik_str"])
    return _CIK.get(ticker.upper())


def parse_form4(xml):
    """[(code, shares, price)] for non-derivative transactions in a Form 4 XML."""
    out = []
    for block in re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", xml, flags=re.S):
        code = re.search(r"<transactionCode>\s*(\w)\s*</transactionCode>", block)
        sh = re.search(r"<transactionShares>\s*<value>\s*([\d.]+)", block)
        px = re.search(r"<transactionPricePerShare>\s*<value>\s*([\d.]+)", block)
        if code and sh:
            out.append((code.group(1), float(sh.group(1)), float(px.group(1)) if px else 0.0))
    return out


def insider_activity(ticker, days=30, max_filings=8, today=None):
    """{'buys': n, 'buy_value': $, 'sells': n, 'sell_value': $, 'insiders': n} or None."""
    cik = _cik(ticker)
    if not cik:
        return None
    sub, _ = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    r = sub["filings"]["recent"]
    cutoff = ((today or datetime.now(timezone.utc).date()) - timedelta(days=days)).isoformat()
    rows = [i for i in range(len(r["form"])) if r["form"][i] == "4" and r["filingDate"][i] >= cutoff][:max_filings]
    res = {"buys": 0, "buy_value": 0.0, "sells": 0, "sell_value": 0.0, "insiders": 0, "filings": len(rows)}
    people = set()
    for i in rows:
        doc = r["primaryDocument"][i].split("/")[-1]
        acc = r["accessionNumber"][i].replace("-", "")
        try:
            xml, _ = _get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}", accept_json=False)
        except (urllib.error.URLError, TimeoutError, ValueError):
            continue
        time.sleep(0.15)  # stay well under SEC's 10 requests/second
        owner = re.search(r"<rptOwnerName>(.*?)</rptOwnerName>", xml)
        for code, sh, px in parse_form4(xml):
            if code == "P":
                res["buys"] += 1
                res["buy_value"] += sh * px
            elif code == "S":
                res["sells"] += 1
                res["sell_value"] += sh * px
            else:
                continue
            if owner:
                people.add(owner.group(1).strip())
    res["insiders"] = len(people)
    return res


# ---------------------------------------------------------------- developers (GitHub)

def _commit_count(repo, since, until):
    """Commits on the default branch in [since, until), via the Link header of a 1-per-page listing."""
    repo, _, branch = repo.partition("@")  # "owner/name@branch" when work happens off the default branch
    url = f"https://api.github.com/repos/{repo}/commits?per_page=1&since={since}T00:00:00Z&until={until}T00:00:00Z" + (f"&sha={branch}" if branch else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        link = r.headers.get("Link") or ""
        body = json.loads(r.read().decode())
    m = re.search(r'[?&]page=(\d+)>; rel="last"', link)
    return int(m.group(1)) if m else len(body)


def dev_activity(ticker, today=None):
    """(commits in the last 28 days, average per 28 days over the prior 84) or None."""
    repo = REPOS.get(ticker)
    if not repo:
        return None
    today = today or datetime.now(timezone.utc).date()
    d28, d112 = today - timedelta(days=28), today - timedelta(days=112)
    last = _commit_count(repo, d28.isoformat(), today.isoformat())
    prior = _commit_count(repo, d112.isoformat(), d28.isoformat()) / 3
    return last, prior


def refresh_dev(data, force=False):
    """Recompute developer activity at most once per UTC day and cache it in the data file."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cache = data.get("devActivity") or {}
    if cache.get("date") == day and not force:
        return cache
    with ThreadPoolExecutor(max_workers=4) as pool:
        res = dict(zip(REPOS, pool.map(lambda t: _safe(dev_activity, t), REPOS)))
    vals = {t: list(v) for t, v in res.items() if v}
    if len(vals) >= len(REPOS) // 2:  # only replace a cache with a mostly-successful run
        data["devActivity"] = {"date": day, "values": vals}
    return data.get("devActivity") or {}


# ---------------------------------------------------------------- retail interest

def coinbase_rank():
    data, _ = _get("https://itunes.apple.com/us/rss/topfreeapplications/limit=200/genre=6015/json")
    for i, e in enumerate(data["feed"]["entry"]):
        if "coinbase" in e["im:name"]["label"].lower():
            return i + 1
    return None  # not in the top 200


def record_rank(data):
    """Store today's Coinbase rank in the crypto data file (called when a session is saved)."""
    try:
        rank = coinbase_rank()
    except Exception:  # noqa: BLE001 - optional
        return
    hist = data.setdefault("coinbaseRank", [])
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hist[:] = [h for h in hist if h["date"] != day] + [{"date": day, "rank": rank}]
    del hist[:-90]


# ---------------------------------------------------------------- briefing

def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001 - every signal is optional
        return None


def _money(v):
    return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}k"


def briefing_text(cfg, data, prices):
    held = list(data["state"]["positions"])
    watch = [t for t in cfg["growth"] + cfg["core"] if t in WIKI]
    out = ["", "SIGNALS (free alternative data - context only; none of these is a catalyst by itself, confirm with news first):"]

    # public attention
    with ThreadPoolExecutor(max_workers=6) as pool:
        att = dict(zip(watch, pool.map(lambda t: _safe(wiki_attention, t), watch)))
    got = {t: v for t, v in att.items() if v}
    if got:
        notable = sorted((t for t, (ratio, _) in got.items() if ratio >= 1.5 or ratio <= 0.6 or t in held),
                         key=lambda t: -got[t][0])
        flags = [f"{t} {got[t][0]:.1f}x{' (SPIKE)' if got[t][0] >= 2 else ''}{' [held]' if t in held else ''}" for t in notable]
        out.append("  Public attention (Wikipedia views, last 7 days vs prior 28-day average; 1.0x = normal): "
                   + (", ".join(flags) if flags else "nothing unusual") + ".")
    else:
        out.append("  Public attention: unavailable this session.")

    if cfg["key"] == "us":
        movers = sorted((t for t in watch if prices.get(t, {}).get("ok")), key=lambda t: -abs(prices[t].get("chg") or 0))[:6]
        names = list(dict.fromkeys(held + movers))
        with ThreadPoolExecutor(max_workers=3) as pool:
            ins = dict(zip(names, pool.map(lambda t: _safe(insider_activity, t), names)))
        parts = []
        for t, v in ins.items():
            if not v:
                continue
            if v["buys"] or v["sells"]:
                bits = []
                if v["buys"]:
                    bits.append(f"{v['buys']} BUY transaction{'s' if v['buys'] != 1 else ''} {_money(v['buy_value'])}")
                if v["sells"]:
                    bits.append(f"{v['sells']} sale transaction{'s' if v['sells'] != 1 else ''} {_money(v['sell_value'])}")
                parts.append(f"{t}: {', '.join(bits)} ({v['insiders']} insider{'s' if v['insiders'] != 1 else ''})")
            else:
                parts.append(f"{t}: none")
        if parts:
            out.append("  Insider trades (SEC Form 4, last 30 days, open-market only; held names + today's biggest movers): " + "; ".join(parts) + ".")
            out.append("    Executive sales are usually pre-scheduled (10b5-1 plans) and say little; open-market BUYS, especially by several insiders, are the meaningful signal.")
        else:
            out.append("  Insider trades: unavailable this session.")

    if cfg["key"] == "crypto":
        cache = data.get("devActivity") or {}
        dev = cache.get("values") or {}  # refreshed once a day when a crypto session is saved
        parts = []
        for t, v in dev.items():
            if not v:
                continue
            last4, prior = v
            ratio = last4 / prior if prior else None
            tag = "" if ratio is None else (" (RISING)" if ratio >= 1.4 else " (FALLING)" if ratio <= 0.6 else "")
            parts.append(f"{t} {last4} vs {prior:.0f}{tag}")
        out.append("  Developer activity (GitHub commits on the main repo, last 28 days vs the average 28 days over the prior 84; slow-moving, a multi-month signal): "
                   + ("; ".join(parts) + f" (as of {cache.get('date')})" if parts else "first reading is collected when this session is saved") + ".")
        hist = data.get("coinbaseRank") or []
        now = _safe(coinbase_rank)
        if now or hist:
            week = next((h["rank"] for h in reversed(hist) if h["date"] <= (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()), None)
            trend = f", was #{week} a week ago" if week else " (no week-ago reading yet)"
            out.append(f"  Retail interest: Coinbase is #{now if now else '200+'} in the US App Store Finance free chart{trend}. "
                       "A climbing rank has historically lined up with retail crypto euphoria (often late in a rally).")
    return "\n".join(out)
