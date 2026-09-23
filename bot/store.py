"""JSON persistence. One file per market: {state, nav_history, trades}."""
import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data")


def path(key):
    return os.path.join(DATA_DIR, f"{key}.json")


def load(key):
    with open(path(key), encoding="utf-8") as f:
        return json.load(f)


def save(key, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path(key) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path(key))
