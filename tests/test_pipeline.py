from conftest import FakeHttp
from docs_watchdog.classify import ClassificationError
from docs_watchdog.extractors import SourceExtractor
from docs_watchdog.fetch import SourceError
from docs_watchdog.models import Connector, Finding, Source
from docs_watchdog.pipeline import run_pipeline

URL = "https://x/doc.md"
V1 = "# Changelog\n" + "\n".join(f"entry {i} describes something long enough" for i in range(10))
V2 = V1 + "\nv16 will be sunset on 2027-02-01 for all apps."


def wrap(text):
    return f"<html><body><pre>{text}</pre></body></html>"


def setup(pages):
    conn = Connector("Meta Ads", 1, True, [Source("s", "markdown", URL, min_chars=20)])
    return [conn], SourceExtractor(FakeHttp(pages))


def finding(*a):
    return [Finding(a[1], a[2], a[3], "deprecation", "high", "v16", "2027-02-01",
                    "v16 will be sunset on 2027-02-01", "migrate", origin="ai")]


def test_first_run_creates_baseline_then_second_detects_change(store):
    conns, ex = setup({URL: wrap(V1)})
    seen = []
    clf = lambda *a: (seen.append(a), finding(*a))[1]
    r1 = run_pipeline(conns, store, ex, clf)
    assert "baseline created" in r1.status[0] and not seen

    conns, ex = setup({URL: wrap(V2)})
    r2 = run_pipeline(conns, store, ex, clf)
    # deterministic finding + the AI layer's finding: the AI never replaces the rules' result
    assert sorted(f.origin for f in r2.findings) == ["ai", "rules"]
    diff_text, connector, source_id, url = seen[0]
    assert diff_text == "[ADDED] (under: Changelog)\nv16 will be sunset on 2027-02-01 for all apps."  # only the change
    assert (connector, source_id, url) == ("Meta Ads", "s", URL)  # traceable to source


def test_unchanged_second_run_does_not_classify(store):
    conns, ex = setup({URL: wrap(V1)})
    run_pipeline(conns, store, ex, finding)
    r = run_pipeline(conns, store, ex, lambda *a: 1 / 0)
    assert r.status == ["Meta Ads/s: no changes"]


def test_failed_source_preserves_snapshot_and_creates_no_baseline(store):
    conns, ex = setup({URL: SourceError("network down")})
    r = run_pipeline(conns, store, ex, finding)
    assert store.latest("Meta Ads", "s") is None and "network down" in r.errors[0]

    conns, ex = setup({URL: wrap(V1)})
    run_pipeline(conns, store, ex, finding)
    conns, ex = setup({URL: SourceError("boom")})
    run_pipeline(conns, store, ex, finding)
    assert store.latest("Meta Ads", "s") == V1  # untouched by the failure


def test_empty_extraction_is_error_not_baseline(store):
    conns, ex = setup({URL: wrap("")})
    r = run_pipeline(conns, store, ex, finding)
    assert r.errors and store.latest("Meta Ads", "s") is None


def test_one_failing_connector_does_not_stop_others(store):
    ok = Connector("Good", 2, True, [Source("s", "markdown", "https://x/ok.md", min_chars=20)])
    bad = Connector("Bad", 1, True, [Source("s", "markdown", "https://x/bad.md", min_chars=20)])
    ex = SourceExtractor(FakeHttp({"https://x/ok.md": wrap(V1), "https://x/bad.md": SourceError("x")}))
    r = run_pipeline([bad, ok], store, ex, finding)
    assert len(r.errors) == 1 and store.latest("Good", "s") == V1


def test_classification_failure_keeps_baseline_so_change_is_retried(store):
    conns, ex = setup({URL: wrap(V1)})
    run_pipeline(conns, store, ex, finding)
    conns, ex = setup({URL: wrap(V2)})

    def bad(*a):
        raise ClassificationError("API down")

    r = run_pipeline(conns, store, ex, bad)
    assert store.latest("Meta Ads", "s") == V1 and "API down" in r.errors[0]
    assert [f.origin for f in r.findings] == ["rules"]  # the deterministic result is not lost
    r = run_pipeline(conns, store, ex, finding)  # next run succeeds
    assert sorted(f.origin for f in r.findings) == ["ai", "rules"]
    assert store.latest("Meta Ads", "s") == V2


def test_default_mode_is_deterministic_and_advances_baseline(store):
    conns, ex = setup({URL: wrap(V1)})
    run_pipeline(conns, store, ex)
    conns, ex = setup({URL: wrap(V2)})
    r = run_pipeline(conns, store, ex)  # no classifier at all: rules only, no credentials
    assert [f.change_type for f in r.findings] == ["deprecation"]
    assert r.findings[0].deadline == "2027-02-01" and r.findings[0].origin == "rules"
    assert store.latest("Meta Ads", "s") == V2


def test_dry_run_writes_nothing(store):
    conns, ex = setup({URL: wrap(V1)})
    run_pipeline(conns, store, ex, finding, dry_run=True)
    assert store.latest("Meta Ads", "s") is None
