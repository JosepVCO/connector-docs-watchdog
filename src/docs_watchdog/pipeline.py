from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from .classify import ClassificationError
from .diff import MAX_CHUNK_CHARS, chunk_changes, compute_changes
from .extractors import SourceExtractor
from .fetch import EmptyContentError, FetchError, SourceError
from .models import Connector, Finding, Source
from .rules import classify_changes

log = logging.getLogger("docs_watchdog.pipeline")

MAX_GROWTH_RATIO = 4.0  # a source growing more than this is reported (not blocked)


# Per-source outcomes. Anything in FAILED means the source was NOT successfully processed.
FAILED = {"fetch_failed", "extraction_failed", "incomplete_source", "classification_failed"}


@dataclass
class SourceRun:
    """What happened to one source in this run, with the provenance of what was fetched."""

    connector: str
    source_id: str
    url: str
    outcome: str  # baseline_created | no_changes | changed | one of FAILED
    detail: str = ""
    method: str = ""  # http | browser | bundled-file
    final_url: str = ""
    http_status: int | None = None
    fetched_at: str = ""
    raw_bytes: int = 0
    chars: int = 0
    fingerprint: str = ""
    attempts: int = 1
    used_fallback: bool = False


@dataclass
class RunResult:
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # anything that makes the run incomplete
    status: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # run is complete, but look at this
    unverified: list[str] = field(default_factory=list)  # sources configured `verified: false`
    sources_total: int = 0
    sources_ok: int = 0  # extracted and fully processed
    sources: list[SourceRun] = field(default_factory=list)


def _failure_kind(e: SourceError) -> str:
    if isinstance(e, FetchError):
        return "fetch_failed"
    if isinstance(e, EmptyContentError):
        return "incomplete_source"
    return "extraction_failed"


def _provenance(rec: SourceRun, extractor, failed: bool = False) -> None:
    fetch = getattr(extractor, "last_fetch", None)
    if fetch is not None:
        rec.method, rec.final_url, rec.http_status = fetch.method, fetch.final_url, fetch.status
        rec.fetched_at, rec.raw_bytes, rec.attempts = fetch.fetched_at, fetch.raw_bytes, fetch.attempts
    ext = getattr(extractor, "last", None)
    if ext is not None and not failed:
        rec.chars, rec.fingerprint, rec.used_fallback = ext.chars, ext.fingerprint, ext.used_fallback
        if ext.fetch is not None:
            rec.method = ext.fetch.method


def _length_problem(old: str, new: str, src: Source) -> str | None:
    if src.min_length_ratio and len(new) < len(old) * src.min_length_ratio:
        return (f"extraction shrank from {len(old)} to {len(new)} chars "
                f"(< {src.min_length_ratio:.0%} of the baseline): probably a partial page; "
                "baseline preserved. If the page really shrank, set `min_length_ratio: 0` "
                "for this source for one run")
    return None


def run_pipeline(
    connectors: list[Connector],
    store,
    extractor: SourceExtractor,
    classifier: Callable[..., list[Finding]] | None = None,
    dry_run: bool = False,
    today: date | None = None,
) -> RunResult:
    """Deterministic by default: every change becomes rule-based findings with no credentials.
    `classifier` is the optional AI layer; its findings are ADDED to the deterministic ones.

    A baseline advances only when extraction, comparison, deterministic classification and (if
    enabled) the AI pass all finished. `dry_run` never writes snapshots."""
    res = RunResult()
    for conn in connectors:
        for src in conn.sources:
            tag = f"{conn.name}/{src.id}"
            res.sources_total += 1
            if not src.verified:
                res.unverified.append(f"{tag}: configured `verified: false` ({src.url})")
            log.info("processing %s", tag)
            rec = SourceRun(conn.name, src.id, src.url, "")
            res.sources.append(rec)
            try:
                text = extractor.extract(src)
            except SourceError as e:
                _provenance(rec, extractor, failed=True)
                rec.outcome, rec.detail = _failure_kind(e), str(e)
                log.error("%s: %s: %s", tag, rec.outcome, e)
                res.errors.append(f"{tag}: {rec.outcome.replace('_', ' ')}: {e}")
                res.status.append(f"{tag}: ERROR ({rec.outcome.replace('_', ' ')}, snapshot preserved)")
                continue
            _provenance(rec, extractor)
            log.info("%s: extracted %d chars", tag, len(text))
            old = store.latest(conn.name, src.id)

            if old is None:
                if not dry_run:
                    store.save(conn.name, src.id, text, src.url)
                log.info("%s: baseline %s", tag, "would be created" if dry_run else "created")
                res.sources_ok += 1
                rec.outcome = "baseline_created"
                res.status.append(f"{tag}: baseline {'(dry run)' if dry_run else 'created'}")
                continue

            problem = _length_problem(old, text, src)
            if problem:
                log.error("%s: %s", tag, problem)
                rec.outcome, rec.detail = "incomplete_source", problem
                res.errors.append(f"{tag}: incomplete source: {problem}")
                res.status.append(f"{tag}: ERROR (length anomaly, snapshot preserved)")
                continue
            if old and len(text) > len(old) * MAX_GROWTH_RATIO and len(old) >= 1000:
                res.warnings.append(f"{tag}: content grew from {len(old)} to {len(text)} chars "
                                    "in one run; check that the source is still the same page")

            changes = compute_changes(old, text, structural=src.type == "openapi")
            if not changes:
                log.info("%s: no changes", tag)
                res.sources_ok += 1
                rec.outcome = "no_changes"
                res.status.append(f"{tag}: no changes")
                continue
            log.info("%s: %d change(s) detected", tag, len(changes))

            det = classify_changes(changes, conn.name, src.id, src.url, today)
            rec.outcome = "changed"
            if dry_run:
                res.findings += det
                res.sources_ok += 1
                res.status.append(f"{tag}: changed, {len(changes)} change(s), {len(det)} finding(s) (dry run)")
                continue

            ai: list[Finding] = []
            if classifier is not None:
                try:
                    for chunk in chunk_changes(changes, MAX_CHUNK_CHARS):
                        ai += classifier(chunk, conn.name, src.id, src.url)
                except ClassificationError as e:
                    # The deterministic findings are complete and stay in the report; the baseline
                    # does not advance so the AI pass is retried on the same change next run.
                    log.error("%s: AI enhancement failed: %s", tag, e)
                    res.findings += det
                    rec.outcome, rec.detail = "classification_failed", f"AI enhancement failed: {e}"
                    res.errors.append(f"{tag}: AI enhancement failed ({e}); deterministic findings "
                                      "are included, baseline not advanced, will retry")
                    res.status.append(f"{tag}: changed, {len(det)} deterministic finding(s), "
                                      "AI ERROR (will retry)")
                    continue
                finally:
                    for r in getattr(classifier, "rejected", []) or []:
                        res.warnings.append(f"{tag}: AI finding rejected by validation: {r}")
                    if hasattr(classifier, "rejected"):
                        classifier.rejected.clear()
            res.findings += det + ai
            store.save(conn.name, src.id, text, src.url)
            res.sources_ok += 1
            res.status.append(f"{tag}: changed, {len(det) + len(ai)} finding(s)")
    return res
