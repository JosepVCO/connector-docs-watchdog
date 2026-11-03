import json

import pytest

from docs_watchdog.classify import ClassificationError, classify, parse_findings

DIFF = "Bundle campaigns are deprecated. Takes effect for every version on May 19, 2026."


def payload(**over):
    f = {"change_type": "deprecation", "severity": "high", "summary": "s", "impact": "i",
         "affected_area": "campaigns", "confirmed": True, "deadline": "2026-05-19",
         "evidence": "Takes effect for every version on May 19, 2026.",
         "recommended_action": "migrate", "confidence": 0.9}
    f.update(over)
    return json.dumps({"findings": [f]})


def parse(p, text=DIFF):
    return parse_findings(p, "Meta Ads", "s", "http://u", source_text=text)


def test_valid_finding_keeps_evidence_deadline_and_source():
    f = parse(payload())[0]
    assert f.deadline == "2026-05-19" and f.evidence_verified and f.confirmed
    assert f.source_url == "http://u" and f.confidence == 0.9


def test_invented_evidence_drops_deadline_and_caps_confidence():
    f = parse(payload(evidence="Totally made up sentence 2026."))[0]
    assert not f.evidence_verified and f.deadline is None and f.confidence <= 0.5
    assert not f.confirmed


def test_deadline_not_in_evidence_is_discarded():
    assert parse(payload(deadline="2031-01-01"))[0].deadline is None


def test_unconfirmed_high_is_downgraded():
    assert parse(payload(confirmed=False))[0].severity == "medium"


@pytest.mark.parametrize("bad", ["not json", '{"findings": "x"}', "{}", '{"findings": [}'])
def test_malformed_responses_raise(bad):
    with pytest.raises(ClassificationError):
        parse(bad)


def test_invalid_enum_dropped():
    assert parse(payload(severity="catastrophic")) == []


def test_confidence_clamped_and_garbage_tolerated():
    assert parse(payload(confidence=7))[0].confidence == 1.0
    assert parse(payload(confidence="n/a"))[0].confidence == 0.5


def test_api_error_wrapped_without_leaking():
    class C:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("boom")

    with pytest.raises(ClassificationError, match="boom"):
        classify("some diff", "c", "s", "u", client=C)


def test_empty_diff_makes_no_api_call():
    assert classify("  ", "c", "s", "u", client=None) == []
