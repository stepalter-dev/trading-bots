# Paper-trading bots (US, ASX, crypto)

Three fake-money bots with a shared dashboard on GitHub Pages.

- `bot/` - the code. `engine.py` is a deterministic referee: every hard rule (position caps, 5% cash buffer, trade counts, day-trade flatten) is enforced in code, whoever proposes the trades.
- `docs/index.html` - the live dashboard. It reads `docs/data/{us,asx,crypto}.json`.
- `docs/data/*.json` - state, NAV history and trade log per bot. Each session commits an update here.

## How it runs today (free): Claude routines

Each bot is a scheduled Claude routine. A fire does:

1. `git clone` this repo, then `python3 -m bot.session prepare <market>` - prints `SKIP: ...` (wrong hour, weekend, market closed, duplicate) or a briefing with the strategy rules, positions and prices.
2. Claude researches with web search and writes a decision JSON.
3. `python3 -m bot.session apply <market> /tmp/decision.json` - the referee applies it, updates the data file, posts to Discord and pushes the data to this repo with `GH_TOKEN`.

No dashboard-database tool is used, so there are no approval prompts. Strategy and rules live in this repo (`bot/brain.py`, `bot/engine.py`, `bot/markets.py`), so tuning = editing here.

`alt-github-actions/` holds an alternative that runs fully on GitHub's scheduler using the Anthropic API (paid). It is parked; move the files to `.github/workflows/` and add `ANTHROPIC_API_KEY` + `DISCORD_WEBHOOK` secrets to use it.

Local tests: `python -m pytest tests`; `python -m bot.session prepare asx --force`; `python -m bot.session apply asx decision.json --force --dry`.

## Paid alternative setup (parked)


1. **GitHub repo**: create a new **public** repo (e.g. `trading-bots`), empty (no README). Public is required for free GitHub Pages; the data is fake money.
2. **Anthropic API key**: console.anthropic.com -> API keys -> create key. Add credit and set a monthly spend limit (Settings -> Limits). This is billed separately from a Claude subscription.
3. **Repo secrets** (repo -> Settings -> Secrets and variables -> Actions -> New repository secret):
   - `ANTHROPIC_API_KEY` = your key
   - `DISCORD_WEBHOOK` = your Discord webhook URL
   - Optional variable `BOT_MODEL` (Variables tab) to change model. Default `claude-sonnet-5`; `claude-haiku-4-5-20251001` is much cheaper but weaker.
4. **Pages**: Settings -> Pages -> Deploy from a branch -> `main` / `/docs`. Your dashboard is then at `https://<you>.github.io/<repo>/`.
5. **Test**: Actions tab -> `run-crypto` -> Run workflow -> tick "force". Check Discord and the dashboard.

## How it decides when to run

Schedules fire hourly-ish; the bot only does real work at its session slots (local time): US 10/12/14/15 New York, ASX 10/12/14/15 Sydney, crypto 02/08/14/20 UTC. It skips weekends, holidays (stale benchmark quote) and duplicates, and adjusts for daylight saving automatically. GitHub scheduled runs can start a few minutes late.

## Tuning

Watchlists and slots: `bot/markets.py`. Trading rules: `bot/engine.py`. Strategy prompt: `bot/brain.py`.
Local tests: `pip install -r requirements.txt pytest`, `python -m pytest tests`, `python -m bot.run us --force --dry --stub`.

## Trading costs (assumed)

Every fill pays slippage and a fee, defined per market in `bot/markets.py` (`costs`):

| Market | Slippage | Fee |
|---|---|---|
| US | 0.05% | $0.005/share, min $1, max 1% |
| ASX | 0.08% | 0.08% of value, min A$6 |
| Crypto | 0.10% | 0.26% of value |

Buys fill above the quote and sells below it. Fees are part of the cost basis, so realised P&L is net of costs. These are assumptions modelled on discount brokers, not a quote from any real broker; history before 2026-09-24 was recorded cost-free. Trade records carry `refPrice` (the quote), `price` (the fill) and `fee`.
