"""Routine-driven runner (free path): Claude itself is the decision-maker.

    python -m bot.session prepare <market> [--force]
        Runs the gates and fetches prices. Prints either `SKIP: <why>` (stop here) or
        the full briefing: strategy rules, positions, prices. Claude then researches the
        news and writes a decision JSON file.

    python -m bot.session apply <market> <decision.json> [--force] [--dry]
        Referee applies the decision, updates the data file, posts to Discord and (unless
        --dry / --no-push) uploads the data file to GitHub via the REST API. Needs GH_TOKEN in the
        environment only for local testing; DISCORD_WEBHOOK for Discord.
"""
import argparse
import json
import os
import sys

from . import brain, core, engine
from .markets import MARKETS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def push_data(key):
    """Upload docs/data/<key>.json to GitHub through the REST contents API.

    No git credentials in the sandbox are needed: if the environment's API-credential
    proxy injects the token for api.github.com, no Authorization header is required here.
    A GH_TOKEN env var is used as a header only when present (local testing).
    """
    import base64
    import urllib.error
    import urllib.request

    repo = os.environ.get("GITHUB_REPO", "stepalter-dev/trading-bots")
    rel = f"docs/data/{key}.json"
    url = f"https://api.github.com/repos/{repo}/contents/{rel}"
    with open(os.path.join(ROOT, rel), "rb") as f:
        content = base64.b64encode(f.read()).decode("ascii")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "paper-trading-bot", "Content-Type": "application/json"}
    token = os.environ.get("GH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def call(method, u, body=None):
        req = urllib.request.Request(u, data=json.dumps(body).encode() if body else None, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    for attempt in range(4):
        try:
            sha = call("GET", url + "?ref=main").get("sha")
            call("PUT", url, {"message": f"{key}: session", "content": content, "sha": sha, "branch": "main"})
            print("pushed data to GitHub")
            return True
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            print(f"push attempt {attempt + 1} failed: HTTP {e.code} {detail}")
            if e.code in (401, 403, 404):
                return False  # auth/permission problem - retrying will not help
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"push attempt {attempt + 1} failed: {e}")
    return False


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["prepare", "apply"])
    ap.add_argument("market", choices=sorted(MARKETS))
    ap.add_argument("decision", nargs="?")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)

    try:
        s = core.begin(args.market, force=args.force)
    except core.Skip as why:
        print(f"SKIP: {why}. Nothing to do this fire - stop here.")
        return 0

    if args.mode == "prepare":
        if s["feed_failed"]:
            print(f"FEED_FAILED: {len(s['failed'])} of {len(s['prices'])} price fetches failed. Do not research or trade. "
                  f'Write {{"notes": "", "trades": []}} to a decision file and run apply so the failure is recorded.')
            return 0
        state = s["data"]["state"]
        engine.mark_to_market(state, s["prices"])
        cfg = s["cfg"]
        print(brain.SYSTEM.format(label=cfg["label"], max_swing=3, max_dt=2))
        print("\n========== BRIEFING ==========")
        print(brain.build_context(state, cfg, s["prices"], s["data"]["trades"], engine.nav_of(state), s["bench"], s["slot"], s["is_last"], s["now_local"]))
        print("\n========== YOUR TASK ==========")
        print("Research the news with web search, decide, then save ONLY the JSON object described above (no code fence needed) to a file, e.g. /tmp/decision.json, and run the `apply` command.")
        return 0

    if not args.decision:
        ap.error("apply needs a decision.json path")
    with open(args.decision, encoding="utf-8") as f:
        raw = f.read()
    try:
        decision = brain._extract_json(raw)
    except ValueError:
        print("Could not parse the decision file; treating as hold.")
        decision = {"notes": "Decision file unreadable - held; no trades.", "trades": []}
    decision.setdefault("trades", [])
    print(core.finish(s, decision, dry=args.dry))
    if not args.dry and not args.no_push:
        ok = push_data(args.market)
        if not ok:
            print("WARNING: data was not pushed to GitHub")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
