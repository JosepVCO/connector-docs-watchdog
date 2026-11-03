"""Offline, reproducible demonstration of the full workflow (no network, no credentials).

Runs the real pipeline twice over bundled sample data: stage1 creates baselines, stage2
simulates vendor changes. It uses the default deterministic engine (no AI at all) and the
report is stamped as demonstration output.

Data: everything under demo_data/ is SYNTHETIC and illustrative. It is written in the style of a
changelog and does not reproduce any vendor's documentation. `platform-a-changelog` has a deprecation
section added in stage 2; `example-changelog` is a second invented API.
"""
from __future__ import annotations

from pathlib import Path

from .extractors import SourceExtractor
from .fetch import FetchRecord, utc_now_iso
from .models import Connector, Source
from .pipeline import RunResult, run_pipeline
from .report import render
from .store import Store

DATA = Path(__file__).parent / "demo_data"


class DirFetcher:
    """HttpFetcher stand-in serving files from a stage directory by URL basename."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.last: FetchRecord | None = None

    def get(self, url: str) -> str:
        path = self.directory / url.rsplit("/", 1)[-1]
        text = path.read_text()
        self.last = FetchRecord("bundled-file", url, final_url=f"demo_data/{path.parent.name}/{path.name}",
                                fetched_at=utc_now_iso(), raw_bytes=len(text))
        return text


def demo_connectors() -> list[Connector]:
    return [
        Connector("Sample Platform A (synthetic)", 1, True, [
            Source("changelog", "markdown", "https://demo.invalid/platform-a-changelog.md", verified=True, min_chars=50)]),
        Connector("Example Ads API (synthetic)", 2, True, [
            Source("changelog", "markdown", "https://demo.invalid/example-changelog.md", verified=True, min_chars=50)]),
    ]


def run_demo() -> tuple[str, RunResult]:
    store, connectors = Store(Path(":memory:")), demo_connectors()
    run_pipeline(connectors, store, SourceExtractor(DirFetcher(DATA / "stage1")))  # baselines
    res = run_pipeline(connectors, store, SourceExtractor(DirFetcher(DATA / "stage2")))
    text = render(res.findings, res.errors, res.status, demo=True, data_origin="demo",
                  source_runs=res.sources, warnings=res.warnings,
                  unverified=res.unverified, sources_total=res.sources_total,
                  sources_ok=res.sources_ok)
    return text, res
