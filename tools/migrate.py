"""One-off: turn the exported artifact-database documents into docs/data/<market>.json."""
import glob, json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXP = os.path.join(ROOT, "_export")
OUT = os.path.join(ROOT, "docs", "data")
os.makedirs(OUT, exist_ok=True)


def fix(s):
    if isinstance(s, str) and "â€" in s:
        try:
            return s.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s.replace("â€”", "-")
    return s


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return fix(o)


def load(path):
    with open(path, encoding="utf-8") as f:
        return clean(json.load(f))


for key, suffix in (("us", ""), ("asx", "_asx"), ("crypto", "_crypto")):
    state = load(os.path.join(EXP, "portfolio", f"state{suffix}.json"))
    hist = []
    for p in glob.glob(os.path.join(EXP, f"nav_history{suffix}", "*.json")):
        h = load(p)
        if "spyClose" in h:
            h["benchmarkClose"] = h.pop("spyClose")
        hist.append(h)
    hist.sort(key=lambda h: h["date"])
    trades = []
    for p in glob.glob(os.path.join(EXP, f"trades{suffix}", "*.json")):
        t = load(p)
        t["id"] = os.path.splitext(os.path.basename(p))[0]
        trades.append(t)
    trades.sort(key=lambda t: t["date"])
    with open(os.path.join(OUT, f"{key}.json"), "w", encoding="utf-8") as f:
        json.dump({"state": state, "nav_history": hist, "trades": trades}, f, ensure_ascii=False, indent=1)
    print(key, "state cash", state["cash"], "| nav days", len(hist), "| trades", len(trades))
