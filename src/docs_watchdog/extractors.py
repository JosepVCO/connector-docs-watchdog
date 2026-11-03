from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Callable

from .browser import BrowserSession
from .extract import feed_to_text, html_to_text, markdown_to_text, normalize, openapi_to_text
from .fetch import EmptyContentError, FetchRecord, HttpFetcher, SourceError
from .models import Source

log = logging.getLogger("docs_watchdog.extractors")

# type -> function(raw HTTP body) -> normalized text. `playwright` is handled separately.
STRATEGIES: dict[str, Callable[[str], str]] = {
    "http": html_to_text,
    "html": html_to_text,
    "markdown": markdown_to_text,
    "rss": feed_to_text,
    "openapi": openapi_to_text,
}


def register_strategy(name: str, fn: Callable[[str], str]) -> None:
    """Add a connector-specific extraction strategy (usable as `type: <name>` in connectors.yaml)."""
    if name in STRATEGIES or name == "playwright":
        raise ValueError(f"extraction strategy {name!r} already exists")
    STRATEGIES[name] = fn


@dataclass
class Extraction:
    """Provenance of one successful extraction (what the report shows)."""

    fetch: FetchRecord | None
    strategy: str
    chars: int
    fingerprint: str  # sha256 of the extracted text; a fingerprint, not the content
    used_fallback: bool = False


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def clean(text: str, source: Source) -> str:
    """Per-source cleanup requested in the connector definition: drop volatile sections
    (e.g. an auto-generated page summary) and boilerplate lines."""
    if source.drop_sections:
        wanted = {s.strip().lower() for s in source.drop_sections}
        out, skipping, level = [], False, 0
        for line in text.splitlines():
            m = _HEADING.match(line)
            if m:
                if skipping and len(m.group(1)) <= level:
                    skipping = False
                if not skipping and m.group(2).strip().lower() in wanted:
                    skipping, level = True, len(m.group(1))
                    continue
            if not skipping:
                out.append(line)
        text = "\n".join(out)
    if source.ignore_patterns:
        pats = [re.compile(p) for p in source.ignore_patterns]
        text = "\n".join(l for l in text.splitlines() if not any(p.fullmatch(l.strip()) for p in pats))
    return normalize(text)


class SourceExtractor:
    """Turns a Source into normalized text using its configured strategy (+ fallback).

    Independent of storage, diffing, classification and reporting. After each call `last` holds the
    provenance of a successful extraction and `last_fetch` the last fetch attempt (also on failure).
    """

    def __init__(self, http: HttpFetcher, browser: BrowserSession | None = None):
        self.http = http
        self.browser = browser
        self.last: Extraction | None = None
        self.last_fetch: FetchRecord | None = None

    def _run(self, source: Source) -> tuple[str, FetchRecord | None]:
        if source.type == "playwright":
            if self.browser is None:
                raise SourceError("no browser session available")
            try:
                return normalize(self.browser.render_text(source.url, source.selector)), None
            finally:
                self.last_fetch = getattr(self.browser, "last", None)
        try:
            return STRATEGIES[source.type](self.http.get(source.url)), None
        finally:
            self.last_fetch = getattr(self.http, "last", None)

    def extract(self, source: Source) -> str:
        """Return text, or raise SourceError (EmptyContentError if incomplete)."""
        self.last = self.last_fetch = None
        return self._extract(source, fallback_used=False)

    def _extract(self, source: Source, fallback_used: bool) -> str:
        try:
            log.info("%s: extracting via %s", source.id, source.type)
            text = clean(self._run(source)[0], source)
            if len(text) < source.min_chars:
                raise EmptyContentError(
                    f"{source.type} extraction returned {len(text)} chars (< {source.min_chars})"
                )
            for marker in source.expect_text:
                if marker not in text:
                    raise EmptyContentError(
                        f"expected text {marker!r} not found: the page structure may have changed "
                        "or the fetch was incomplete")
            self.last = Extraction(self.last_fetch, source.type, len(text), fingerprint(text),
                                   fallback_used)
            return text
        except SourceError as e:
            if source.fallback is None:
                raise
            log.warning("%s: %s failed (%s); trying fallback %s", source.id, source.type, e,
                        source.fallback.type)
            return self._extract(source.fallback, fallback_used=True)
        except Exception as e:  # parser bugs on unexpected structure must not crash the run
            err = SourceError(f"unexpected {source.type} extraction failure: {e!r}")
            if source.fallback is None:
                raise err from e
            log.warning("%s: %s; trying fallback", source.id, err)
            return self._extract(source.fallback, fallback_used=True)
