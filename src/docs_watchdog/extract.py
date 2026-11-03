from __future__ import annotations

import html
import re

import feedparser
import trafilatura

from .fetch import SourceError

_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_STAMP_VALUE = (
    rf"(?:{_MONTH}\.? \d{{1,2}}(?:st|nd|rd|th)?,? \d{{4}}"  # May 21, 2026
    rf"|\d{{1,2}}(?:st|nd|rd|th)? {_MONTH}\.?,? \d{{4}}"  # 21 May 2026
    r"|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}"  # 2026-05-21, 05/21/2026
    r"|\d+ (?:minute|hour|day|week|month|year)s? ago|today|yesterday)"
)

# Volatile / UI-only lines. They are dropped ONLY when the whole line is such a marker:
# a sentence that merely starts with "Updated on ..." is real content and must survive.
_NOISE_LINES = re.compile(
    r"^(?:was this helpful\??|copy for llm|view as markdown|on this page|share|print"
    rf"|(?:last\s+)?(?:updated|modified|reviewed)(?:\s+on)?\s*[:\-]?\s*{_STAMP_VALUE}\.?)$",
    re.I,
)


def normalize(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l and not _NOISE_LINES.match(l))


def html_to_text(raw: str) -> str:
    return normalize(
        trafilatura.extract(raw, output_format="markdown", include_links=False, include_tables=True)
        or ""
    )


def feed_to_text(xml: str) -> str:
    feed = feedparser.parse(xml)
    return normalize(
        "\n\n".join(
            f"## {e.get('title', '')} ({e.get('published', '')})\n{e.get('summary', '')}"
            for e in feed.entries
        )
    )


def markdown_to_text(raw: str) -> str:
    """Meta serves docs as Markdown wrapped in a minimal HTML page (<pre> or bare body)."""
    m = re.search(r"<body[^>]*>(.*)</body>", raw, re.S | re.I)
    body = m.group(1) if m else raw
    body = re.sub(r"</?pre[^>]*>", "", body)
    if re.search(r"<(script|div|iframe|html)\b", body, re.I):
        # e.g. Meta answers unknown .md URLs with HTTP 200 and an HTML "not found" app shell
        raise SourceError("unexpected structure: HTML markup in a Markdown source (soft 404?)")
    return normalize(html.unescape(body))


def openapi_to_text(raw: str) -> str:
    """Canonical, line-per-fact rendering (see openapi.py) so operations, parameters, bodies,
    responses, schema fields and deprecations diff structurally."""
    from . import openapi

    return openapi.render(raw)


def extract_text(raw: str, source_type: str) -> str:
    """Kept for backwards compatibility; dispatches on the content-type strategies."""
    return {
        "rss": feed_to_text,
        "markdown": markdown_to_text,
        "openapi": openapi_to_text,
    }.get(source_type, html_to_text)(raw)
