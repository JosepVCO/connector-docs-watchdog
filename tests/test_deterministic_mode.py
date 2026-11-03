import json

from conftest import FakeHttp
from docs_watchdog.classify import AIClassifier
from docs_watchdog.extractors import SourceExtractor
from docs_watchdog.fetch import SourceError
from docs_watchdog.models import Connector, Source
from docs_watchdog.pipeline import run_pipeline
from docs_watchdog.report import render
from docs_watchdog.store import Store

from pathlib import Path

BASE = "# Changelog\n" + "\n".join(f"entry {i} is a plain, long enough changelog sentence" for i in range(8))
NEW = BASE + "\nThe v1 endpoint is deprecated and will be removed on 2027-03-31.\nA new optional field was added."


def conn(*sources):
    return [Connector("Vendor", 1, True, list(sources))]


def src(id_="a", url="https://x/a.md", **kw):
    return Source(id_, "markdown", url, verified=kw.pop("verified", True), min_chars=20, **kw)


def pages(**by_url):
    return SourceExtractor(FakeHttp({u: f"<html><body><pre>{t}</pre></body></html>" if isinstance(t, str) else t
                                     for u, t in by_url.items()}))


def full_run(texts):
    store = Store(Path(":memory:"))
    run_pipeline(conn(src()), store, pages(**{"https://x/a.md": texts[0]}))
    res = run_pipeline(conn(src()), store, pages(**{"https://x/a.md": texts[1]}))
    return res, render(res.findings, res.errors, res.status, warnings=res.warnings,
                       unverified=res.unverified, sources_total=res.sources_total,
                       sources_ok=res.sources_ok)


def test_complete_report_without_any_credentials(monkeypatch):
    for k in ("LLM_PROVIDER", "LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    res, report = full_run([BASE, NEW])
    assert "Run status: COMPLETE" in report and "deterministic (no AI)" in report
    assert "## Deprecations (1)" in report and "2027-03-31" in report
    for label in ("Provider / product", "Detected", "Source", "Description", "Potential impact",
                  "Evidence", "New state", "Deadline", "Status"):
        assert label in report
    assert "Not determined" in report  # impact is never invented
    assert all(f.origin == "rules" and f.classifier == "deterministic-rules" for f in res.findings)


def test_report_is_reproducible():
    _, a = full_run([BASE, NEW])
    _, b = full_run([BASE, NEW])
    assert a == b


def test_no_changes_still_states_the_review_completed():
    _, report = full_run([BASE, BASE])
    assert "No relevant changes detected" in report and "checked successfully" in report
    assert "Run status: COMPLETE" in report


def test_failed_source_makes_the_run_visibly_incomplete():
    store = Store(Path(":memory:"))
    cs = conn(src("a", "https://x/a.md"), src("b", "https://x/b.md"))
    run_pipeline(cs, store, pages(**{"https://x/a.md": BASE, "https://x/b.md": BASE}))
    res = run_pipeline(cs, store, pages(**{"https://x/a.md": NEW, "https://x/b.md": SourceError("HTTP 503")}))
    report = render(res.findings, res.errors, res.status, sources_total=res.sources_total,
                    sources_ok=res.sources_ok)
    assert "INCOMPLETE" in report and "Vendor/b: extraction failed: HTTP 503" in report
    assert "## Errors" in report and "1 of 2" in report
    assert "COMPLETE." not in report.replace("INCOMPLETE", "")


def test_unverified_sources_are_listed():
    store = Store(Path(":memory:"))
    cs = conn(src(verified=False))
    res = run_pipeline(cs, store, pages(**{"https://x/a.md": BASE}))
    report = render(res.findings, res.errors, res.status, unverified=res.unverified,
                    sources_total=res.sources_total, sources_ok=res.sources_ok)
    assert "## Sources not verified" in report and "verified: false" in report


def test_report_sections_separate_change_categories():
    store = Store(Path(":memory:"))
    spec = lambda paths, schemas: json.dumps({"openapi": "3.0.0", "info": {"version": "1"}, "paths": paths,
                                              "components": {"schemas": schemas}})
    ok = {"responses": {"200": {"description": "ok"}}}
    old = spec({"/a": {"get": {**ok, "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}]}},
                "/gone": {"get": ok}, "/dep": {"get": ok}}, {"S": {"type": "object", "properties": {"x": {"type": "string"}}}})
    new = spec({"/a": {"get": {**ok, "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string"}},
                                                    {"name": "z", "in": "query", "schema": {"type": "string"}}]}},
                "/new": {"get": ok}, "/dep": {"get": {**ok, "deprecated": True}}},
               {"S": {"type": "object", "properties": {"x": {"type": "string"}, "y": {"type": "string"}}}})
    s = Source("api", "openapi", "https://x/o.json", verified=True, min_chars=5)
    run_pipeline(conn(s), store, SourceExtractor(FakeHttp({"https://x/o.json": old})))
    res = run_pipeline(conn(s), store, SourceExtractor(FakeHttp({"https://x/o.json": new})))
    report = render(res.findings, res.errors, res.status, sources_total=1, sources_ok=1)
    for heading in ("Potentially breaking changes", "Removals", "Deprecations",
                    "Parameter and schema changes", "New functionality"):
        assert f"## {heading}" in report
    assert "GET /gone" in report and "Previous state" in report


# ---- AI-enhanced mode stays compatible and additive ----------------------------------------
class Provider:
    name, is_demo = "fake-llm", False

    def __init__(self, payload):
        self.payload = payload

    def complete(self, system, user):
        assert "[ADDED]" in user  # labelled changes, not a bare added-lines blob
        return self.payload


def test_ai_enhanced_adds_to_deterministic_findings_and_keeps_evidence():
    ai = json.dumps({"findings": [{
        "change_type": "deprecation", "severity": "high", "summary": "v1 retired", "impact": "Connectors on v1 break",
        "affected_area": "v1", "confirmed": True, "deadline": "2027-03-31",
        "evidence": "The v1 endpoint is deprecated and will be removed on 2027-03-31.",
        "recommended_action": "Migrate", "confidence": 0.9}]})
    store = Store(Path(":memory:"))
    run_pipeline(conn(src()), store, pages(**{"https://x/a.md": BASE}))
    res = run_pipeline(conn(src()), store, pages(**{"https://x/a.md": NEW}), AIClassifier(Provider(ai)))
    assert sorted(f.origin for f in res.findings if f.change_type == "deprecation") == ["ai", "rules"]
    ai_f = next(f for f in res.findings if f.origin == "ai")
    assert ai_f.evidence_verified and ai_f.classifier == "fake-llm" and ai_f.impact == "Connectors on v1 break"
    report = render(res.findings, res.errors, res.status, classifier="fake-llm")
    assert "AI-enhanced" in report and "AI interpretation" in report and "deterministic-rules" in report


def test_ai_failure_does_not_lose_the_deterministic_report():
    class Down:
        name, is_demo = "down", False

        def complete(self, s, u):
            raise ConnectionError("provider unreachable")

    store = Store(Path(":memory:"))
    run_pipeline(conn(src()), store, pages(**{"https://x/a.md": BASE}))
    res = run_pipeline(conn(src()), store, pages(**{"https://x/a.md": NEW}), AIClassifier(Down()))
    report = render(res.findings, res.errors, res.status, classifier="down")
    assert "2027-03-31" in report and "INCOMPLETE" in report and "provider unreachable" in report
    assert store.latest("Vendor", "a") == BASE  # will be retried
