import json

from docs_watchdog.classify import classify, parse_findings
from docs_watchdog.diff import make_diff
from docs_watchdog.extract import extract_text, normalize
from docs_watchdog.report import render

OLD = "# Changelog\n\nv17 released.\nv16 supported."
NEW = "# Changelog\n\nv17 released.\nv16 will be sunset on 2026-02-01.\nv16 supported."


def test_normalize_strips_date_noise():
    assert "Last updated" not in normalize("Last updated: May 3, 2026\nhello")


def test_extract_html():
    html = "<html><body><nav>menu</nav><article><p>" + "API v16 deprecated. " * 20 + "</p></article></body></html>"
    assert "deprecated" in extract_text(html, "html")


def test_diff_only_additions():
    d = make_diff(OLD, NEW)
    assert "sunset" in d and "released" not in d


def test_diff_identical_is_empty():
    assert make_diff(OLD, OLD) == ""


class FakeClient:
    class messages:
        @staticmethod
        def create(**kw):
            payload = {"findings": [{
                "change_type": "deprecation", "severity": "high", "affected_area": "API v16",
                "confirmed": True, "deadline": "2026-02-01", "quote": "v16 will be sunset on 2026-02-01.",
                "recommended_action": "Migrate to v17."}]}
            class M:  # noqa
                content = [type("B", (), {"text": "```json\n" + json.dumps(payload) + "\n```"})]
            return M


def test_classify_backtest_flags_sunset_high():
    fs = classify(make_diff(OLD, NEW), "Google Ads", "sunset", "http://x", client=FakeClient)
    assert fs[0].severity == "high" and fs[0].deadline == "2026-02-01"


def test_parse_empty():
    assert parse_findings('{"findings": []}', "c", "s", "u") == []


def test_report_sorted_and_contains_evidence():
    fs = classify(make_diff(OLD, NEW), "Google Ads", "sunset", "http://x", client=FakeClient)
    out = render(fs)
    assert "[HIGH]" in out and "v16 will be sunset" in out
