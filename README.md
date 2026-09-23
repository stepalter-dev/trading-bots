# Paper-trading bots (US, ASX, crypto)

Three fake-money bots that run **unattended on GitHub Actions** - no approvals, no PC needed.

- `bot/` - the code. Claude (with web search) proposes trades; `engine.py` enforces every hard rule in code (position caps, 5% cash buffer, trade counts, day-trade flatten).
- `docs/index.html` - the live dashboard (GitHub Pages). It reads `docs/data/{us,asx,crypto}.json`.
- `docs/data/*.json` - the bots' state, NAV history and trade log. Every session commits an update here.
- `.github/workflows/run-*.yml` - the schedules.

## One-time setup

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
