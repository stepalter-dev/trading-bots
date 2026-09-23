"""API-driven runner (GitHub Actions + Anthropic API path).

    python -m bot.run <us|asx|crypto> [--force] [--dry] [--stub]

--force  ignore the slot/weekday/duplicate/market-open gates (for manual tests)
--dry    do everything but do not save data or post to Discord
--stub   skip the Claude call and just hold (offline tests)

The free path (Claude routines) uses bot/session.py instead.
"""
import argparse
import sys

from . import brain, core
from .markets import MARKETS


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("market", choices=sorted(MARKETS))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--stub", action="store_true")
    args = ap.parse_args(argv)

    try:
        s = core.begin(args.market, force=args.force)
    except core.Skip as why:
        print(f"[{args.market}] skipped: {why}")
        return 0
    print(f"[{args.market}] fetched {len(s['prices']) - len(s['failed'])}/{len(s['prices'])} prices; failed: {s['failed']}")

    decision = {"notes": "", "trades": []}
    if not s["feed_failed"]:
        state = s["data"]["state"]
        from . import engine
        engine.mark_to_market(state, s["prices"])
        context = brain.build_context(state, s["cfg"], s["prices"], s["data"]["trades"], engine.nav_of(state), s["bench"], s["slot"], s["is_last"], s["now_local"])
        try:
            decision = {"notes": "Stub run - holding.", "trades": []} if args.stub else brain.decide(s["cfg"], context)
        except Exception as e:  # noqa: BLE001 - a brain failure must never corrupt state
            print("Decision step failed:", repr(e))
            decision = {"notes": f"Decision step failed this session ({type(e).__name__}) - holding; no trades.", "trades": []}
    print(core.finish(s, decision, dry=args.dry))
    return 0


if __name__ == "__main__":
    sys.exit(main())
