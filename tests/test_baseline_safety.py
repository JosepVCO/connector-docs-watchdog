import json

import pytest

from conftest import FakeHttp
from docs_watchdog.classify import AIClassifier, ClassificationError, classify
from docs_watchdog.diff import MAX_CHUNK_CHARS
from docs_watchdog.extractors import SourceExtractor
from docs_watchdog.fetch import SourceError
from docs_watchdog.llm import LLMTruncatedError
from docs_watchdog.models import Connector, Source
from docs_watchdog.pipeline import run_pipeline
from docs_watchdog.store import FileStore

URL = "https://x/doc.md"
LONG = "\n".join(f"entry {i}: a reasonably long changelog sentence number {i}" for i in range(60))


def wrap(t):
    return f"<html><body><pre>{t}</pre></body></html>"


def setup(text, **src_kw):
    src = Source("s", "markdown", URL, verified=True, min_chars=20, **src_kw)
    return [Connector("V", 1, True, [src])], SourceExtractor(FakeHttp({URL: wrap(text) if not isinstance(text, Exception) else text}))


# ---- incomplete / abnormal extractions never replace a good baseline -----------------------
def test_partial_page_does_not_replace_baseline_and_is_an_error(store):
    conns, ex = setup(LONG)
    run_pipeline(conns, store, ex)
    partial = "\n".join(LONG.splitlines()[:10]) + "\nA brand new line appeared at the end."
    conns, ex = setup(partial)
    r = run_pipeline(conns, store, ex)
    assert store.latest("V", "s") == LONG  # baseline untouched
    assert r.errors and "partial page" in r.errors[0] and not r.findings
    conns, ex = setup(LONG)  # page is whole again: no flood of false "additions"
    r = run_pipeline(conns, store, ex)
    assert r.status == ["V/s: no changes"] and not r.errors


def test_length_check_can_be_disabled_per_source(store):
    conns, ex = setup(LONG)
    run_pipeline(conns, store, ex)
    conns, ex = setup("\n".join(LONG.splitlines()[:10]) + "\nnew line", min_length_ratio=0)
    r = run_pipeline(conns, store, ex)
    assert not r.errors and r.findings and store.latest("V", "s").endswith("new line")


def test_abnormal_growth_is_a_visible_warning(store):
    conns, ex = setup(LONG)
    run_pipeline(conns, store, ex)
    conns, ex = setup(LONG + "\n" + LONG.replace("entry", "item") * 5)
    r = run_pipeline(conns, store, ex)
    assert r.warnings and "grew" in r.warnings[0] and not r.errors


def test_failed_extraction_never_advances_and_is_reported_as_error(store):
    conns, ex = setup(LONG)
    run_pipeline(conns, store, ex)
    conns, ex = setup(SourceError("503 from vendor"))
    r = run_pipeline(conns, store, ex)
    assert store.latest("V", "s") == LONG and "503" in r.errors[0]
    assert r.sources_total == 1 and r.sources_ok == 0


# ---- the AI layer is complete-or-retry, and never costs the deterministic result ------------
class Provider:
    name, is_demo = "fake-llm", False

    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def complete(self, system, user):
        self.calls.append(user)
        return self.fn(user)


EMPTY = json.dumps({"findings": []})


def big_change():
    return LONG + "\n" + "\n".join(f"filler {i} " + "z" * 80 for i in range(400)) + \
        "\nThe v3 API is deprecated and ends on 2027-05-05 for all apps."


def test_every_chunk_reaches_the_ai_and_baseline_advances_only_when_all_succeed(store):
    conns, ex = setup(LONG)
    run_pipeline(conns, store, ex)
    prov = Provider(lambda u: EMPTY)
    conns, ex = setup(big_change())
    r = run_pipeline(conns, store, ex, AIClassifier(prov))
    assert len(prov.calls) > 1 and not r.errors
    assert sum(c.count("filler") for c in prov.calls) == 400
    assert any("ends on 2027-05-05" in c for c in prov.calls)
    assert store.latest("V", "s") == big_change()


def test_failure_on_a_later_chunk_keeps_baseline_and_keeps_deterministic_findings(store):
    conns, ex = setup(LONG)
    run_pipeline(conns, store, ex)
    n = {"i": 0}

    def flaky(user):
        n["i"] += 1
        if n["i"] == 2:
            raise RuntimeError("rate limited")
        return EMPTY

    conns, ex = setup(big_change())
    r = run_pipeline(conns, store, ex, AIClassifier(Provider(flaky)))
    assert store.latest("V", "s") == LONG
    assert r.errors and "rate limited" in r.errors[0] and "will retry" in r.errors[0]
    assert [f.change_type for f in r.findings if f.change_type == "deprecation"] == ["deprecation"]


def test_truncated_llm_response_is_retried_on_halves():
    text = "\n".join(f"[ADDED]\nline {i} with some words" for i in range(40))

    def fn(user):
        if user.count("line ") > 10:
            raise LLMTruncatedError("max tokens")
        return EMPTY

    prov = Provider(fn)
    assert classify(text, "c", "s", "u", provider=prov) == []
    assert len(prov.calls) > 2  # split until the pieces fit


def test_unrecoverable_truncation_raises_instead_of_returning_partial_results():
    prov = Provider(lambda u: (_ for _ in ()).throw(LLMTruncatedError("max tokens")))
    with pytest.raises(ClassificationError, match="truncated"):
        classify("[ADDED]\none single line", "c", "s", "u", provider=prov)


def test_invalid_ai_findings_are_surfaced_not_silently_dropped(store):
    conns, ex = setup(LONG)
    run_pipeline(conns, store, ex)
    bad = json.dumps({"findings": [{"change_type": "catastrophe", "severity": "high", "evidence": "x"}]})
    conns, ex = setup(LONG + "\nA new optional field was added to the reporting endpoint.")
    r = run_pipeline(conns, store, ex, AIClassifier(Provider(lambda u: bad)))
    assert any("rejected by validation" in w for w in r.warnings) and not r.errors


def test_chunk_size_constant_is_sane():
    assert 1000 <= MAX_CHUNK_CHARS <= 50000


# ---- file-based store (used by the scheduled workflow) -------------------------------------
def test_file_store_matches_store_semantics(tmp_path):
    fs = FileStore(tmp_path)
    assert fs.latest("Meta Ads", "changelog") is None
    assert fs.save("Meta Ads", "changelog", "abc", "http://u") is True
    assert fs.save("Meta Ads", "changelog", "abc", "http://u") is False
    assert fs.latest("Meta Ads", "changelog") == "abc"
    with pytest.raises(ValueError):
        fs.save("Meta Ads", "changelog", "  ")
    meta = json.loads((tmp_path / "snapshots" / "meta-ads" / "changelog.json").read_text())
    assert meta["source_url"] == "http://u" and len(meta["sha256"]) == 64
    assert not list(tmp_path.rglob("*.tmp"))


def test_pipeline_works_with_file_store(tmp_path):
    fs = FileStore(tmp_path)
    conns, ex = setup(LONG)
    run_pipeline(conns, fs, ex)
    conns, ex = setup(LONG + "\nv1 will be sunset on 2027-02-01 for every app.")
    r = run_pipeline(conns, fs, ex)
    assert r.findings[0].deadline == "2027-02-01" and fs.latest("V", "s").endswith("every app.")
