"""Offline extraction tests over the fixtures in tests/fixtures (provenance: MANIFEST.json).

Two kinds of fixture, never mixed up:
  excerpt    real vendor text reduced to what the tests need (only Google, which states CC BY 4.0)
  synthetic  invented text on the HTML structure of the real page (Meta, Microsoft/LinkedIn)
Nothing here is a live fetch; live behaviour is exercised by running the CLI.
"""
import json
import re
from pathlib import Path

import pytest

from conftest import FakeHttp
from docs_watchdog.config import load_connectors
from docs_watchdog.diff import compute_changes
from docs_watchdog.extractors import SourceExtractor
from docs_watchdog.fetch import SourceError
from docs_watchdog.rules import classify_changes

ROOT = Path(__file__).parent.parent
FIX = Path(__file__).parent / "fixtures"
MANIFEST = json.loads((FIX / "MANIFEST.json").read_text())["fixtures"]
CONNECTORS = {c.id: c for c in load_connectors(ROOT / "connectors.yaml")}


def entry(connector, source_id):
    return next(e for e in MANIFEST if e["connector"] == connector and e["source_id"] == source_id)


def source(e):
    return next(s for s in CONNECTORS[e["connector"]].sources if s.id == e["source_id"])


def raw(e):
    return (FIX / e["file"]).read_text(encoding="utf-8")


def extract(e, body=None):
    return SourceExtractor(FakeHttp({source(e).url: raw(e) if body is None else body})).extract(source(e))


ALL = pytest.mark.parametrize("e", MANIFEST, ids=lambda e: f"{e['origin']}:{e['connector']}/{e['source_id']}")
HTML = pytest.mark.parametrize("e", [e for e in MANIFEST if e["type"] == "html"],
                               ids=lambda e: f"{e['connector']}/{e['source_id']}")


# ---- provenance and redistribution hygiene ---------------------------------------------------
@pytest.mark.parametrize("e", [e for e in MANIFEST if e["origin"] == "excerpt"], ids=lambda e: e["source_id"])
def test_excerpts_record_full_provenance(e):
    assert e["source_url"].startswith("https://") and e["http_status"] == 200
    assert e["captured_at"].startswith("2026-") and len(e["original_sha256"]) == 64
    assert "CC BY 4.0" in e["license"] and e["kept_sections"] and e["removed"]
    header = raw(e)[:1200]  # the file itself carries attribution, source, hash and a change notice
    assert e["source_url"] in header and e["original_sha256"] in header
    assert "Creative Commons Attribution 4.0" in raw(e) and "Changes:" in header


@pytest.mark.parametrize("e", [e for e in MANIFEST if e["origin"] == "synthetic"], ids=lambda e: e["source_id"])
def test_synthetic_fixtures_are_labelled_and_modelled_on_a_url(e):
    assert e["modeled_on"].startswith("https://") and e["structure"]
    assert "SYNTHETIC" in raw(e) and "original_sha256" not in e  # no claim of being a capture


def test_only_google_is_real_vendor_text():
    """Meta and Microsoft/LinkedIn: no redistribution grant found, so those fixtures are synthetic."""
    assert {e["connector"] for e in MANIFEST if e["origin"] == "excerpt"} == {"google-ads"}
    assert {e["connector"] for e in MANIFEST if e["origin"] == "synthetic"} == {"meta-ads", "linkedin-marketing"}


@ALL
def test_fixtures_stay_small(e):
    assert (FIX / e["file"]).stat().st_size < 12_000  # excerpts, not page copies


# ---- extraction quality, per fixture --------------------------------------------------------
@ALL
def test_extracts_enough_content_and_passes_completeness_checks(e):
    text = extract(e)  # raises if an expect_text end-of-page marker is missing
    assert len(text) >= e["min_chars"]
    assert source(e).expect_text and all(m in text for m in source(e).expect_text)
    assert len(re.findall(r"(?m)^#{2,4} ", text)) >= e["min_headings"]


@ALL
def test_truncated_page_is_rejected_not_stored_as_a_baseline(e):
    body = raw(e)
    # Either the wrapper is broken (extraction failure) or the end-of-page marker is missing
    # (incomplete source). Both are SourceErrors, which the pipeline never turns into a baseline.
    with pytest.raises(SourceError):
        extract(e, body[: len(body) // 3])


@ALL
def test_extraction_is_stable(e):
    assert extract(e) == extract(e)  # nothing volatile survives cleanup


@HTML
def test_headings_are_not_lost_in_html_extraction(e):
    body = raw(e)
    main = re.search(r"<main.*?</main>", body, re.S | re.I)
    main = re.sub(r"<(script|style|nav|footer|header)\b.*?</\1>", "", main.group(0) if main else body,
                  flags=re.S | re.I)
    dropped = {d.lower() for d in source(e).drop_sections}  # removed on purpose, not lost
    titles = [re.sub(r"<[^>]+>|\s+", " ", t).strip().lower()
              for t in re.findall(r"<h[23]\b[^>]*>(.*?)</h[23]>", main, re.S)]
    in_page = [t for t in titles if t not in dropped]
    in_text = len(re.findall(r"^#{2,3} ", extract(e), re.M))
    assert in_page and in_text >= 0.95 * len(in_page), (e["source_id"], len(in_page), in_text)


# ---- connector-specific cleanup -------------------------------------------------------------
def test_google_page_summary_is_removed():
    t = extract(entry("google-ads", "release-notes"))
    assert "Page Summary" not in t and not t.startswith("- ")
    assert "Page Summary" in raw(entry("google-ads", "release-notes"))  # it is in the page; cleanup removed it


def test_linkedin_style_ui_boilerplate_is_removed_by_ignore_patterns():
    e = entry("linkedin-marketing", "recent-changes")
    assert "requires authorization" in raw(e) and "requires authorization" not in extract(e)


# ---- detection on these fixtures ------------------------------------------------------------
def test_synthetic_removed_notice_is_detected_with_its_deadline():
    e = entry("linkedin-marketing", "recent-changes")
    full = extract(e)
    start = full.index("**Deprecation Notice:**")
    older = full[:start] + full[full.index("## ", start):]
    fs = classify_changes(compute_changes(older, full), "LinkedIn Marketing", "recent-changes", source(e).url)
    sunset = [f for f in fs if f.change_type == "deprecation" and f.deadline == "2027-12-15"]
    assert sunset and "202701" in sunset[0].evidence and sunset[0].confirmed


def test_synthetic_breaking_changes_section_is_found():
    e = entry("linkedin-marketing", "recent-changes")
    full = extract(e)
    start = full.index("### Breaking Changes")
    older = full[:start] + full[full.index("\n### ", start + 5) + 1:]
    fs = classify_changes(compute_changes(older, full), "LinkedIn Marketing", "recent-changes", source(e).url)
    breaking = [f for f in fs if f.change_type == "breaking"]
    assert len(breaking) == 1 and breaking[0].severity == "high"
    assert breaking[0].deadline == "2028-01-31" and "action required" in breaking[0].evidence


def test_synthetic_meta_deprecation_is_found_with_its_deadline():
    e = entry("meta-ads", "versioned-changes-v25")
    full = extract(e)
    assert "from June 30, 2027, for every earlier version" in full
    i = full.index("### Bundled Campaigns")
    older = full[:i] + full[full.index("\n### ", i + 5) + 1:]
    f = [f for f in classify_changes(compute_changes(older, full), "Meta Ads", "v25", source(e).url)
         if f.change_type == "deprecation"]
    assert len(f) == 1 and f[0].deadline == "2027-06-30" and f[0].severity == "high"


def test_google_minor_version_section_is_not_flagged_as_breaking():
    """The excerpt keeps Google's real sentence 'Minor versions ... don't contain breaking changes'."""
    e = entry("google-ads", "release-notes")
    full = extract(e)
    i = full.index("### v24.1 (2026-05-13)")
    older = full[:i] + full[full.index("\n### ", i + 5) + 1:]
    fs = classify_changes(compute_changes(older, full), "Google Ads", "release-notes", source(e).url)
    assert not [f for f in fs if f.change_type == "breaking"]
    assert "v24.1" in "\n".join(f.evidence for f in fs)  # the new section is still reported
