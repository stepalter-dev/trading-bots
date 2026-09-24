import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import learning
from bot.markets import MARKETS


def _data():
    trades = [
        {"id": "b1", "ticker": "AAA", "action": "buy", "shares": 10, "price": 100, "date": "2026-09-10T01:00:00Z", "bucket": "growth", "rationale": "earnings beat", "horizon": "Q4 report"},
        {"id": "s1", "ticker": "AAA", "action": "sell", "shares": 10, "price": 110, "date": "2026-09-15T01:00:00Z", "bucket": "growth", "rationale": "hit target", "realizedPnL": 100.0, "realizedPnLPct": 10.0},
        {"id": "s2", "ticker": "BBB", "action": "sell", "shares": 5, "price": 95, "date": "2026-09-16T01:00:00Z", "bucket": "daytrade", "rationale": "flatten", "realizedPnL": -25.0, "realizedPnLPct": -5.0},
    ]
    state = {"cash": 1000.0, "startingCash": 1000.0, "positions": {"AAA": {"shares": 1, "avgCost": 100, "lastPrice": 90, "bucket": "growth"}}}
    return {"state": state, "trades": trades, "nav_history": [{"date": "2026-09-16", "nav": 1010, "benchmarkNav": 1005}]}


def test_ensure_backfills_journal_from_sells():
    d = learning.ensure(_data())
    assert [j["id"] for j in d["journal"]] == ["s1", "s2"]
    j = d["journal"][0]
    assert j["whyBought"] == "earnings beat" and j["horizon"] == "Q4 report" and j["daysHeld"] == 5
    assert d["journal"][1]["whyBought"] == ""  # no matching buy
    assert learning.ensure(d) is d and len(d["journal"]) == 2  # idempotent


def test_scorecard_stats():
    d = learning.ensure(_data())
    txt = learning.scorecard_text(d, MARKETS["us"], 1010.0)
    assert "Closed trades: 2, win rate 50%" in txt
    assert "avg win +10.00%" in txt and "avg loss -5.00%" in txt
    assert "too few to draw conclusions" in txt
    assert "daytrade: 1 closed" in txt


def test_empty_record_says_nothing_to_evaluate():
    d = _data()
    d["trades"] = []
    learning.ensure(d)
    assert "No closed trades yet" in learning.scorecard_text(d, MARKETS["us"], 1000.0)


def test_reviews_and_lessons_validated():
    d = learning.ensure(_data())
    n = learning.apply_reflection(d, {"reviews": [
        {"id": "s1", "verdict": "sound", "lesson": "target discipline worked"},
        {"id": "s2", "verdict": "bogus", "lesson": "x"},      # bad verdict ignored
        {"id": "nope", "verdict": "sound", "lesson": "x"},    # unknown id ignored
    ], "lessons": ["a", "", "b"] + ["z"] * 20})
    assert n == 1 and d["journal"][0]["review"]["verdict"] == "sound" and d["journal"][1]["review"] is None
    assert d["lessons"][:2] == ["a", "b"] and len(d["lessons"]) == learning.MAX_LESSONS
    # a review is written once; omitting lessons leaves them alone
    before = copy.deepcopy(d["lessons"])
    learning.apply_reflection(d, {"reviews": [{"id": "s1", "verdict": "flawed", "lesson": "changed?"}]})
    assert d["journal"][0]["review"]["verdict"] == "sound" and d["lessons"] == before


def test_record_sells_and_briefing_lists_pending():
    d = learning.ensure(_data())
    new_sell = {"id": "s3", "ticker": "AAA", "action": "sell", "shares": 1, "price": 90, "date": "2026-09-17T01:00:00Z", "bucket": "growth", "rationale": "stop", "realizedPnL": -10.0, "realizedPnLPct": -10.0}
    learning.record_sells(d, [new_sell])
    learning.record_sells(d, [new_sell])  # no duplicates
    assert [j["id"] for j in d["journal"]] == ["s1", "s2", "s3"]
    txt = learning.briefing_text(d, MARKETS["us"], 1000.0)
    assert "AWAITING YOUR REVIEW" in txt and "id=s3" in txt and "SCORECARD" in txt
