"""The connector contract: production connectors are config-only; new ones add YAML (+ optional strategy)."""
from pathlib import Path

import pytest

from conftest import FakeHttp
from docs_watchdog.config import ConfigError, load_connectors
from docs_watchdog.diff import compute_changes
from docs_watchdog.extractors import STRATEGIES, SourceExtractor
from docs_watchdog.pipeline import run_pipeline
from docs_watchdog.rules import classify_changes
from docs_watchdog.store import Store

ROOT = Path(__file__).parent.parent


def write(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text(body)
    return p


def test_the_three_production_connectors_are_live_sources_with_stable_ids():
    cs = load_connectors(ROOT / "connectors.yaml")
    assert [c.id for c in cs] == ["meta-ads", "google-ads", "linkedin-marketing"]
    assert all(c.vendor and c.scope and not c.example for c in cs)
    for c in cs:
        for s in c.sources:
            assert s.verified and s.url.startswith("https://") and "invalid" not in s.url
            assert s.type in STRATEGIES or s.type == "playwright"
            assert s.expect_text or s.type == "rss"  # every page source has a completeness check


def test_production_connectors_use_only_shared_strategies():
    """Adding a vendor must not require vendor code: no custom strategies are in use."""
    builtin = {"http", "html", "markdown", "rss", "openapi", "playwright"}
    for c in load_connectors(ROOT / "connectors.yaml"):
        for s in c.sources:
            assert s.type in builtin and (s.fallback is None or s.fallback.type in builtin)


def test_example_connector_is_labelled_and_separate():
    ex = load_connectors(ROOT / "examples/connector.example.yaml")
    assert len(ex) == 1 and ex[0].example and ex[0].id == "example-vendor-ads"
    prod_ids = {c.id for c in load_connectors(ROOT / "connectors.yaml", only_enabled=False)}
    assert ex[0].id not in prod_ids


def test_example_connector_runs_through_the_shared_engine_end_to_end():
    """The template works as documented: YAML + the example strategy, nothing else."""
    conn = load_connectors(ROOT / "examples/connector.example.yaml")
    url = conn[0].sources[0].url
    v1 = "# comment\nExample Vendor changelog\n2026-01-01 added field alpha to the reports\n"
    v2 = v1 + "2026-06-01 The beta endpoint is deprecated and will be removed on 2027-01-31.\n"
    store = Store(Path(":memory:"))
    run_pipeline(conn, store, SourceExtractor(FakeHttp({url: v1})))
    res = run_pipeline(conn, store, SourceExtractor(FakeHttp({url: v2})))
    assert not res.errors and res.findings[0].deadline == "2027-01-31"
    assert "comment" not in store.latest("Example Vendor Ads", "changelog")  # strategy dropped '#' lines


def test_new_options_are_validated(tmp_path):
    base = "connectors:\n  - name: A\n    sources: [{id: a, type: http, url: 'https://x', %s}]\n"
    with pytest.raises(ConfigError, match="expect_text"):
        load_connectors(write(tmp_path, base % "expect_text: foo"))
    with pytest.raises(ConfigError, match="regex"):
        load_connectors(write(tmp_path, base % "ignore_patterns: ['(']"))
    with pytest.raises(ConfigError, match="type"):
        load_connectors(write(tmp_path, base.replace("http", "carrier-pigeon") % "verified: true"))
    ok = load_connectors(write(tmp_path, base % "drop_sections: [Page Summary]"))
    assert ok[0].id == "a" and ok[0].sources[0].drop_sections == ["Page Summary"]


def test_duplicate_connector_ids_are_rejected(tmp_path):
    body = ("connectors:\n  - name: A\n    id: x\n    sources: [{id: a, type: http, url: 'https://x'}]\n"
            "  - name: B\n    id: x\n    sources: [{id: a, type: http, url: 'https://x'}]\n")
    with pytest.raises(ConfigError, match="duplicated"):
        load_connectors(write(tmp_path, body))


def test_only_option_accepts_name_or_id(monkeypatch, tmp_path):
    from typer.testing import CliRunner
    import docs_watchdog.cli as cli

    for k in ("LLM_PROVIDER", "LLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for sel, code in (("meta-ads", 0), ("Meta Ads", 0), ("nope", 2)):
        r = CliRunner().invoke(cli.app, ["--validate-only", "--only", sel])
        assert r.exit_code == code, sel


# ---- strict validation of AI output ----------------------------------------------------------
def test_strict_boolean_and_full_date_validation():
    import json
    from docs_watchdog.classify import parse_findings

    text = "Takes effect for every version on May 19, 2026."
    def parse(**over):
        f = {"change_type": "deprecation", "severity": "high", "evidence": text, "deadline": "2026-05-19",
             "confirmed": True}
        f.update(over)
        rejected = []
        out = parse_findings(json.dumps({"findings": [f]}), "c", "s", "u", source_text=text, rejected=rejected)
        return out[0], rejected

    f, _ = parse()
    assert f.confirmed and f.deadline == "2026-05-19" and f.severity == "high"
    f, rej = parse(confirmed="false")  # a string is not a boolean: never confirms, and is reported
    assert not f.confirmed and f.severity == "medium" and any("boolean" in r for r in rej)
    f, _ = parse()  # same year, different day: the year alone proves nothing
    assert parse(deadline="2026-12-31")[0].deadline is None
    assert parse(confirmed=None)[0].confirmed is False and parse(confirmed=1)[0].confirmed is False
    f = parse_findings(json.dumps({"findings": [{"change_type": "deprecation", "severity": "low", "evidence": text}]}),
                       "c", "s", "u", source_text=text)[0]
    assert f.confirmed is False  # missing means unconfirmed, not "assumed confirmed"


# ---- rules added in this stage ---------------------------------------------------------------
def classify(old, new):
    return classify_changes(compute_changes(old, new), "C", "s", "https://x/doc", None)


def test_one_finding_per_section_and_every_new_section_is_listed():
    old = "# Notes\nintro line that is long enough"
    new = old + ("\n## Feature deprecations and changes\nIndividual feature deprecations are tracked separately here."
                 "\nSee the deprecations guide for upcoming lifecycle details.\n"
                 "## October release\nA new optional field was added to the creatives endpoint.\n"
                 "## November release\nSomething else was documented for the audience network.")
    fs = classify(old, new)
    dep = [f for f in fs if f.change_type == "deprecation"]
    assert len(dep) == 1 and dep[0].affected_area == "Feature deprecations and changes"
    info = [f for f in fs if f.change_type == "informational"]
    assert len(info) == 1 and "October release" in info[0].evidence and "November release" in info[0].evidence


def test_plural_breaking_changes_heading_and_abbreviated_dates():
    old = "intro paragraph that is long enough"
    new = old + "\n### Breaking Changes\n- New required field (action required): starts Dec 15, 2026."
    f = [f for f in classify(old, new) if f.change_type == "breaking"]
    assert len(f) == 1 and f[0].deadline == "2026-12-15"


def test_several_dates_in_one_section_are_not_guessed():
    old = "intro paragraph that is long enough"
    new = old + "\n### Version Sunset\nThese versions will be sunset: 202510 on Oct 15, 2026 and 202511 on Nov 16, 2026."
    f = classify(old, new)[0]
    assert f.change_type == "deprecation" and f.deadline is None and "2 different dates" in f.summary and "none is singled out" in f.summary
