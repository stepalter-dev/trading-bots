"""Routine-driven runner (free path): Claude itself is the decision-maker.

    python -m bot.session prepare <market> [--force]
        Runs the gates and fetches prices. Prints either `SKIP: <why>` (stop here) or
        the full briefing: strategy rules, positions, prices. Claude then researches the
        news and writes a decision JSON file.

    python -m bot.session apply <market> <decision.json> [--force] [--dry]
        Referee applies the decision, updates the data file, posts to Discord and (unless
        --dry / --no-push) commits and pushes the data file to main with git. DISCORD_WEBHOOK for Discord.
"""
import argparse
import json
import os
import sys

from . import brain, core, engine, learning
from .markets import MARKETS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def push_data(key):
    """Commit docs/data/<key>.json and push it to main with plain git.

    The routine sandbox's git remote already carries access once the repo has been
    attached, so no token handling is needed here.
    """
    import subprocess

    rel = f"docs/data/{key}.json"
    ident = ["-c", "user.name=trading-bot", "-c", "user.email=trading-bot@users.noreply.github.com"]

    def git(*a):
        r = subprocess.run(["git", *ident, *a], cwd=ROOT, capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr).strip()

    git("add", rel)
    if git("diff", "--cached", "--quiet")[0] == 0:
        print("no data change to push")
        return True
    code, out = git("commit", "-m", f"{key}: session")
    if code:
        print("commit failed:", out[:200])
        return False
    for attempt in range(4):
        code, out = git("push", "origin", "HEAD:main")
        if code == 0:
            print("pushed data to GitHub")
            return True
        print(f"push attempt {attempt + 1} failed: {out[:200]}")
        git("pull", "--rebase", "-X", "theirs", "origin", "main")
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
        print(brain.format_system(cfg))
        print("\n========== BRIEFING ==========")
        print(brain.build_context(state, cfg, s["prices"], s["data"]["trades"], engine.nav_of(state), s["bench"], s["slot"], s["is_last"], s["now_local"])
              + learning.briefing_text(s["data"], cfg, engine.nav_of(state)))
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
