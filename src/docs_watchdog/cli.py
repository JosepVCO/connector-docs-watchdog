from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import typer

from .browser import BrowserSession
from .classify import AIClassifier
from .config import ConfigError, load_connectors
from .demo import run_demo
from .extractors import SourceExtractor
from .fetch import HttpFetcher
from .llm import LLMConfigError, llm_configured, provider_from_env
from .pipeline import run_pipeline
from .report import render
from .store import FileStore, Store

app = typer.Typer(help="Watch vendor API docs for changes affecting connectors.")
log = logging.getLogger("docs_watchdog")


def _write_report(out: Path, text: str) -> Path:
    """Timestamped report (never overwrites an earlier run) plus a `latest.md` pointer."""
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    path = out / f"{stamp}.md"
    path.write_text(text, encoding="utf-8")
    (out / "latest.md").write_text(text, encoding="utf-8")
    return path


@app.command()
def run(
    config: Path = Path("connectors.yaml"),
    db: Path = Path("data/snapshots.db"),
    state_dir: Path = typer.Option(
        None, help="Keep snapshots as plain-text files under this directory (instead of the "
                   "SQLite --db). Used by the scheduled workflow so state lives in a Git branch."),
    out: Path = Path("reports"),
    only: str = typer.Option(None, help="Only this connector (name or id, e.g. meta-ads)"),
    no_llm: bool = typer.Option(False, help="Force deterministic mode even if an LLM provider is configured"),
    dry_run: bool = typer.Option(False, help="Extract and diff only; write no snapshots or report"),
    demo: bool = typer.Option(False, help="Offline demo on bundled sample data (no credentials, not AI)"),
    validate_only: bool = typer.Option(False, help="Validate connectors.yaml and exit"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Fetch sources, diff against the last snapshot, classify, write a Markdown report.

    Deterministic by default (no API key needed). If LLM_PROVIDER (and LLM_API_KEY) are set, an
    AI interpretation layer is added on top of the deterministic findings."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs may carry tokens
    if demo:
        text, res = run_demo()
        out.mkdir(parents=True, exist_ok=True)
        path = out / "demo-report.md"
        path.write_text(text, encoding="utf-8")
        for line in res.status:
            typer.echo(line)
        log.info("DEMO MODE (deterministic rules, not AI): %d finding(s); report written to %s",
                 len(res.findings), path)
        return
    try:
        connectors = load_connectors(config)
    except ConfigError as e:
        log.error("invalid configuration: %s", e)
        raise typer.Exit(2)
    if only:
        connectors = [c for c in connectors if only in (c.name, c.id)]
        if not connectors:
            log.error("no enabled connector with name or id %r", only)
            raise typer.Exit(2)
    if validate_only:
        log.info("%s OK: %d enabled connector(s)", config, len(connectors))
        return
    provider = None
    if not (no_llm or dry_run) and llm_configured():
        try:
            provider = provider_from_env()  # a configured-but-invalid provider is an error
        except LLMConfigError as e:
            log.error("%s", e)
            raise typer.Exit(2)
    if provider:
        log.info("mode: AI-enhanced (%s%s) on top of the deterministic engine", provider.name,
                 ", DEMO, not AI" if provider.is_demo else "")
    else:
        log.info("mode: deterministic (no AI)")

    http, browser = HttpFetcher(), BrowserSession()
    if dry_run and not (state_dir.exists() if state_dir else db.exists()):
        store = Store(Path(":memory:"))  # a dry run must not create state; with none, every source is a baseline
    else:
        store = FileStore(state_dir) if state_dir else Store(db)
    try:
        res = run_pipeline(
            connectors, store, SourceExtractor(http, browser),
            classifier=None if provider is None else AIClassifier(provider),
            dry_run=dry_run,
        )
    finally:
        browser.close()
        http.close()

    for line in res.status:
        typer.echo(line)
    if dry_run:
        raise typer.Exit(1 if res.errors else 0)
    try:
        path = _write_report(out, render(
            res.findings, res.errors, res.status,
            classifier=provider.name if provider else None,
            demo=bool(provider and provider.is_demo),
            warnings=res.warnings, unverified=res.unverified,
            sources_total=res.sources_total, sources_ok=res.sources_ok,
            data_origin="live", source_runs=res.sources))
    except OSError as e:
        log.error("report generation failed: %s", e)
        raise typer.Exit(1)
    log.info("report written to %s", path)
    if res.errors:
        log.warning("%d problem(s); the run is INCOMPLETE, see report", len(res.errors))
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
