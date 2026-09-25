from datetime import date

from bot import signals

FORM4 = """<ownershipDocument><reportingOwner><reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction><transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>100</value></transactionShares><transactionPricePerShare><value>50.5</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
<nonDerivativeTransaction><transactionCoding><transactionCode>M</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>9</value></transactionShares></transactionAmounts></nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument>"""


def test_parse_form4():
    assert signals.parse_form4(FORM4) == [("P", 100.0, 50.5), ("M", 9.0, 0.0)]


def test_wiki_attention_ratio(monkeypatch):
    views = [100] * 28 + [300] * 7
    monkeypatch.setattr(signals, "_get", lambda url, accept_json=True: ({"items": [{"views": v} for v in views]}, 200))
    ratio, last7 = signals.wiki_attention("NVDA", today=date(2026, 9, 25))
    assert ratio == 3.0 and last7 == 300
    assert signals.wiki_attention("NOPE") is None


def test_briefing_survives_every_source_failing(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(signals, "_get", boom)
    monkeypatch.setattr(signals, "_commit_count", boom)
    from bot.markets import MARKETS
    data = {"state": {"positions": {"BTC-USD": {}}}}
    for key in ("us", "asx", "crypto"):
        txt = signals.briefing_text(MARKETS[key], data, {})
        assert "SIGNALS" in txt and "unavailable" in txt
