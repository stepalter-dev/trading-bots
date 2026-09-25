from datetime import datetime, timedelta, timezone

from bot import vc

NOW = datetime(2026, 9, 28, 0, 10, tzinfo=timezone.utc)
CANDS = [{"id": f"yc-co{i}", "source": "YC Fall 2026", "name": f"Co{i}", "url": f"https://yc.com/co{i}"} for i in range(8)]


def inv(i, cheque=60, **kw):
    d = {"id": f"yc-co{i}", "cheque": cheque, "valuation_usd": 20e6, "p_raise_12m": 35, "p_alive_12m": 70, "thesis": "good team"}
    d.update(kw)
    return d


def test_budget_paces_the_fund():
    data = vc.blank()
    budget, deployable = vc.weekly_budget(data, NOW.date())
    assert deployable == 8000 and round(budget, 2) == round(8000 / 52, 2)


def test_invest_validation_and_budget_cap():
    data = vc.blank()
    deals, notes = vc.apply_decision(data, {"investments": [
        inv(0), inv(1), inv(2, cheque=5),                      # 3rd below minimum
        inv(3, valuation_usd=5e9),                              # silly valuation
        inv(4, p_raise_12m=None),                               # missing forecast
        {"id": "made-up", "name": "X", "cheque": 50, "valuation_usd": 2e7, "p_raise_12m": 30, "p_alive_12m": 60, "thesis": "t"},  # no source_url
        inv(5, cheque=500),                                     # trimmed to the remaining cap
        inv(6),                                                 # budget gone
    ]}, NOW, CANDS)
    ids = [d["id"] for d in deals]
    assert ids == ["yc-co0", "yc-co1", "yc-co5"]
    cap = 8000 / 52 * vc.CFG["budget_flex"]
    assert abs(sum(d["amount"] for d in deals) - cap) < 0.01
    assert len(notes) == 5
    assert len(data["forecasts"]) == 6 and data["state"]["cash"] < 10000
    assert set(c["id"] for c in CANDS) <= set(data["state"]["seen"])


def test_own_research_pick_needs_url_and_is_limited():
    data = vc.blank()
    own = lambda n: {"id": n, "name": n, "source_url": "https://techstars.com/x", "cheque": 30, "valuation_usd": 1.5e7,
                     "p_raise_12m": 30, "p_alive_12m": 60, "thesis": "t"}
    deals, notes = vc.apply_decision(data, {"investments": [own("Alpha"), own("Beta")]}, NOW, CANDS)
    assert [d["id"] for d in deals] == ["own-alpha"] and "max 1" in notes[0]


def test_marks_need_evidence_and_follow_on_rules():
    data = vc.blank()
    vc.apply_decision(data, {"investments": [inv(0, cheque=100)]}, NOW, CANDS)
    h = data["state"]["holdings"]["yc-co0"]
    # no follow-on before a verified raise
    _, notes = vc.apply_decision(data, {"followons": [{"id": "yc-co0", "amount": 100, "valuation_usd": 6e7}]}, NOW, CANDS)
    assert "verified raise" in notes[0]
    # a mark without evidence is refused
    _, notes = vc.apply_decision(data, {"updates": [{"id": "yc-co0", "event": "raised", "valuation_usd": 6e7}]}, NOW, CANDS)
    assert "evidence" in notes[0] and h["markValuation"] == 20e6
    # a verified raise marks up 3x, then a capped follow-on is allowed
    deals, _ = vc.apply_decision(data, {"updates": [{"id": "yc-co0", "event": "raised", "valuation_usd": 6e7, "evidence": "https://techcrunch.com/x"}],
                                        "followons": [{"id": "yc-co0", "amount": 1000, "valuation_usd": 6e7}]}, NOW, CANDS)
    assert round(vc.holding_value(h), 2) == 300 + 200 and h["tranches"][1]["amount"] == 200
    # the raise forecast resolves true
    f = next(f for f in data["forecasts"] if f["event"] == "raise_12m")
    assert f["outcome"] is True


def test_undisclosed_raise_uses_step_up_and_shutdown_zeroes():
    data = vc.blank()
    vc.apply_decision(data, {"investments": [inv(0, cheque=100), inv(1, cheque=100)]}, NOW, CANDS)
    deals, _ = vc.apply_decision(data, {"updates": [
        {"id": "yc-co0", "event": "raised", "evidence": "https://x.com/a"},
        {"id": "yc-co1", "event": "shutdown", "evidence": "https://www.ycombinator.com/companies/co1"}]}, NOW, CANDS)
    hs = data["state"]["holdings"]
    assert deals[0]["assumedMark"] and round(vc.holding_value(hs["yc-co0"])) == 250
    assert vc.holding_value(hs["yc-co1"]) == 0
    alive = next(f for f in data["forecasts"] if f["id"] == "yc-co1" and f["event"] == "alive_12m")
    assert alive["outcome"] is False


def test_acquisition_returns_cash_and_scorecard():
    data = vc.blank()
    vc.apply_decision(data, {"investments": [inv(0, cheque=100)]}, NOW, CANDS)
    cash = data["state"]["cash"]
    vc.apply_decision(data, {"updates": [{"id": "yc-co0", "event": "acquired", "valuation_usd": 8e7, "evidence": "https://news.com/a"}]}, NOW, CANDS)
    assert round(data["state"]["cash"] - cash) == 400 and vc.nav_of(data) == data["state"]["cash"]
    later = NOW + timedelta(days=400)
    vc.resolve_forecasts(data, later.date())
    assert "Brier score" in vc.scorecard(data)


def test_weekly_gate():
    data = vc.blank()
    data["state"]["lastUpdated"] = (NOW - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    orig = vc.load
    vc.load = lambda: data
    try:
        try:
            vc.begin(now=NOW)
            raise AssertionError("should skip")
        except vc.Skip:
            pass
        assert vc.begin(force=True, now=NOW)
    finally:
        vc.load = orig
