import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import review  # noqa: E402


def test_drawdown():
    assert review.drawdown([100, 120, 90, 130]) == -25.0
    assert review.drawdown([100, 101, 102]) == 0.0


def test_review_market_verdicts(monkeypatch):
    hist = [{"date": "2026-09-25", "nav": 100, "benchmarkNav": 100},
            {"date": "2026-10-10", "nav": 97, "benchmarkNav": 92},
            {"date": "2026-12-24", "nav": 110, "benchmarkNav": 105}]
    trades = [{"date": "2026-10-01T00:00:00Z", "price": 10, "refPrice": 9.99, "shares": 10, "fee": 1, "action": "buy"}]
    monkeypatch.setattr(review.store, "load", lambda k: {"nav_history": hist, "trades": trades, "state": {"positions": {}}})
    r = review.review_market("us", {"date": "2026-09-25", "nav": 100, "benchmarkNav": 100})
    assert r["verdict"] == "PASS" and r["return"] == 10.0 and r["benchmark"] == 5.0
    assert r["drawdown"] == -3.0 and r["benchmarkDrawdown"] == -8.0 and r["trades"] == 1 and r["costs"] == 1.1
