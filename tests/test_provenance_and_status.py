import httpx
import pytest

from conftest import FakeHttp
from docs_watchdog.extractors import SourceExtractor, clean, register_strategy
from docs_watchdog.fetch import (BACKOFF_SECONDS, MAX_ATTEMPTS, EmptyContentError, FetchError, HttpFetcher)
from docs_watchdog.models import Connector, Source
from docs_watchdog.pipeline import run_pipeline
from docs_watchdog.report import render
from docs_watchdog.store import Store
from pathlib import Path

PAGE = "# Changelog\n" + "\n".join(f"entry {i} is a plain changelog sentence of useful length" for i in range(10))


def http_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def fetcher(handler, sleeps=None):
    return HttpFetcher(http_client(handler), sleep=(sleeps.append if sleeps is not None else lambda s: None))


def ok_handler(request):
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    return httpx.Response(200, text=f"<html><body><pre>{PAGE}</pre></body></html>",
                          headers={"content-type": "text/html; charset=utf-8", "set-cookie": "secret=1"})


# ---- retries, timeouts, typed failures -----------------------------------------------------
def test_fetch_records_provenance_without_headers_or_body():
    f = fetcher(ok_handler)
    f.get("https://x/doc.md")
    r = f.last
    assert (r.status, r.final_url, r.method, r.attempts) == (200, "https://x/doc.md", "http", 1)
    assert r.fetched_at.endswith("+00:00") and r.raw_bytes > 100 and r.content_type == "text/html"
    assert "secret" not in repr(r)


def test_transient_errors_are_retried_with_bounded_backoff_then_succeed():
    calls, sleeps = {"n": 0}, []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, text="ok")

    f = fetcher(handler, sleeps)
    assert f.get("https://x/a") == "ok" and f.last.attempts == 3
    assert sleeps == list(BACKOFF_SECONDS)


def test_persistent_failure_gives_up_after_max_attempts():
    calls = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(502)

    with pytest.raises(FetchError, match="giving up"):
        fetcher(handler).get("https://x/a")
    assert calls["n"] == MAX_ATTEMPTS


def test_client_errors_are_not_retried_and_timeouts_are_bounded():
    calls = {"n": 0}

    def not_found(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(FetchError, match="HTTP 404"):
        fetcher(not_found).get("https://x/a")
    assert calls["n"] == 1

    def slow(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(FetchError, match="timeout"):
        fetcher(slow).get("https://x/a")


# ---- per-source status in the run and in the report -----------------------------------------
def conn(*sources):
    return [Connector("Vendor", 1, True, list(sources), id="vendor")]


def src(i="a", **kw):
    return Source(i, "markdown", f"https://x/{i}.md", verified=True, min_chars=20, **kw)


class Pages:
    """Extractor whose pages (or errors) are given per URL; records provenance like the real one."""

    def __init__(self, pages):
        self.http = FakeHttp(pages)

    def extractor(self):
        return SourceExtractor(self.http)


def test_each_failure_kind_is_distinguished_and_never_looks_successful():
    store = Store(Path(":memory:"))
    cs = conn(src("fetch"), src("empty"), src("soft404"), src("short", expect_text=["END OF PAGE"]))
    wrap = lambda t: f"<html><body><pre>{t}</pre></body></html>"
    ex = SourceExtractor(FakeHttp({
        "https://x/fetch.md": FetchError("HTTP 503 fetching https://x/fetch.md"),
        "https://x/empty.md": wrap("tiny"),
        "https://x/soft404.md": "<html><body><div>not found</div></body></html>",
        "https://x/short.md": wrap(PAGE),
    }))
    res = run_pipeline(cs, store, ex)
    outcomes = {r.source_id: r.outcome for r in res.sources}
    assert outcomes == {"fetch": "fetch_failed", "empty": "incomplete_source",
                        "soft404": "extraction_failed", "short": "incomplete_source"}
    assert res.sources_ok == 0 and len(res.errors) == 4
    report = render(res.findings, res.errors, res.status, data_origin="live", source_runs=res.sources,
                    sources_total=res.sources_total, sources_ok=res.sources_ok)
    assert "INCOMPLETE" in report and "FETCH FAILED" in report and "INCOMPLETE SOURCE" in report
    assert "EXTRACTION FAILED" in report and "COMPLETE." not in report.replace("INCOMPLETE", "")
    assert all(store.latest("Vendor", s) is None for s in outcomes)  # no baseline from a bad fetch


def test_report_states_data_origin_and_per_source_provenance():
    client = http_client(ok_handler)
    from docs_watchdog.fetch import HttpFetcher as F

    ex = SourceExtractor(F(client, sleep=lambda s: None))
    store = Store(Path(":memory:"))
    res = run_pipeline(conn(src("doc")), store, ex)
    # the pipeline records what the extractor recorded
    r = res.sources[0]
    assert (r.outcome, r.http_status, r.final_url) == ("baseline_created", 200, "https://x/doc.md")
    assert len(r.fingerprint) == 64 and r.chars > 100 and r.fetched_at
    live = render(res.findings, res.errors, res.status, data_origin="live", source_runs=res.sources,
                  sources_total=1, sources_ok=1)
    assert "Data origin: LIVE" in live and "https://x/doc.md" in live and "| 200 |" in live
    assert f"`{r.fingerprint[:12]}`" in live and "Baselines were created" in live
    assert "No relevant changes detected" not in live  # nothing was compared on a baseline run
    demo = render([], data_origin="demo")
    fixture = render([], data_origin="fixture")
    assert "Data origin: DEMO" in demo and "not a monitoring result" in demo
    assert "Data origin: FIXTURE" in fixture and "LIVE" not in demo + fixture
    assert "not stated" in render([])


def test_cli_labels_live_runs_and_demo_runs_differently(monkeypatch, tmp_path):
    from typer.testing import CliRunner
    import docs_watchdog.cli as cli

    for k in ("LLM_PROVIDER", "LLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    demo = CliRunner().invoke(cli.app, ["--demo", "--out", str(tmp_path / "d")])
    assert demo.exit_code == 0 and "Data origin: DEMO" in (tmp_path / "d/demo-report.md").read_text()
    assert "Data origin: LIVE" not in (tmp_path / "d/demo-report.md").read_text()

    cfg = tmp_path / "c.yaml"
    cfg.write_text("connectors:\n  - name: A\n    id: a\n    sources: [{id: s, type: markdown, verified: true, "
                   "url: 'https://x/doc.md', min_chars: 5}]\n")
    monkeypatch.setattr(cli, "HttpFetcher", lambda: HttpFetcher(http_client(ok_handler), sleep=lambda s: None))
    r = CliRunner().invoke(cli.app, ["--config", str(cfg), "--only", "a", "--state-dir", str(tmp_path / "s"),
                                     "--out", str(tmp_path / "r")])
    assert r.exit_code == 0
    report = (tmp_path / "r/latest.md").read_text()
    assert "Data origin: LIVE" in report and "DEMONSTRATION" not in report and "| 200 |" in report


# ---- connector-level options handled by the shared engine -----------------------------------
def test_drop_sections_and_ignore_patterns_clean_volatile_text():
    s = Source("x", "http", "https://x/p", drop_sections=["Page Summary"], ignore_patterns=["Cookie notice.*"])
    text = "## Page Summary\n- AI generated summary\n### Details\nkept\n## Real heading\nCookie notice: accept\nreal body"
    assert clean(text, s) == "## Real heading\nreal body"


def test_expect_text_marks_missing_end_marker_as_incomplete():
    ex = SourceExtractor(FakeHttp({"https://x/a.md": f"<html><body><pre>{PAGE}</pre></body></html>"}))
    with pytest.raises(EmptyContentError, match="expected text"):
        ex.extract(src("a", expect_text=["Archived"]))
    assert ex.extract(src("a", expect_text=["entry 9"]))  # present: fine


def test_custom_strategy_registration_rejects_duplicates():
    with pytest.raises(ValueError):
        register_strategy("markdown", lambda raw: raw)


# ---- deadlines are always shown relative to the report date ----------------------------------
def test_past_deadline_is_never_presented_as_future():
    from datetime import date

    from docs_watchdog.models import Finding

    f = Finding("Meta Ads", "v25", "https://x", "deprecation", "high", "Bundle campaigns", "2026-05-19",
                "Takes effect for every version on May 19, 2026.", "migrate", confirmed=True)
    nov = render([f], today=date(2026, 11, 3))
    assert "ALREADY PASSED" in nov and "168 days before this report" in nov and "probably already in effect" in nov
    may = render([f], today=date(2026, 5, 1))
    assert "in 18 days" in may and "ALREADY PASSED" not in may
    assert "today" in render([f], today=date(2026, 5, 19))
    assert "keyword rules" in nov and "not how much a particular integration is affected" in nov


def test_dry_run_creates_no_files_even_without_existing_state(monkeypatch, tmp_path):
    from typer.testing import CliRunner
    import docs_watchdog.cli as cli

    for k in ("LLM_PROVIDER", "LLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "c.yaml"
    cfg.write_text("connectors:\n  - name: A\n    id: a\n    sources: [{id: s, type: markdown, verified: true, "
                   "url: 'https://x/doc.md', min_chars: 5}]\n")
    monkeypatch.setattr(cli, "HttpFetcher", lambda: HttpFetcher(http_client(ok_handler), sleep=lambda s: None))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    r = CliRunner().invoke(cli.app, ["--config", str(cfg), "--dry-run"])
    assert r.exit_code == 0 and "baseline (dry run)" in r.output
    assert list(work.iterdir()) == []  # no data/ directory, no database, no reports
    r = CliRunner().invoke(cli.app, ["--config", str(cfg), "--dry-run", "--state-dir", str(tmp_path / "st")])
    assert r.exit_code == 0 and not (tmp_path / "st").exists()


def test_mixed_run_does_not_claim_new_baselines_were_compared():
    from docs_watchdog.pipeline import SourceRun

    runs = [SourceRun("V", "a", "https://x/a", "no_changes"), SourceRun("V", "b", "https://x/b", "no_changes"),
            SourceRun("V", "c", "https://x/c", "baseline_created")]
    text = render([], source_runs=runs, sources_total=3, sources_ok=3, data_origin="live")
    assert "in the 2 source(s) that had a baseline" in text and "1 source(s) had no baseline yet" in text
    assert "All 3 source(s) were checked" not in text
    clean = render([], source_runs=runs[:2], sources_total=2, sources_ok=2, data_origin="live")
    assert "All 2 source(s) were checked successfully" in clean  # fully compared runs keep the plain wording


def test_bundled_demo_files_are_not_described_as_redirects():
    from docs_watchdog.demo import run_demo

    text, _ = run_demo()
    assert "(bundled file)" in text and "redirected" not in text
