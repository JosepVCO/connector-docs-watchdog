from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ChangeType = Literal[
    "deprecation", "breaking", "removal", "schema", "new", "informational", "cosmetic", "none"
]
Severity = Literal["high", "medium", "low", "none"]

# deprecation: announced/flagged for retirement   removal: something stopped existing
# breaking: potentially incompatible change       schema: parameter/field/type/response change
# new: additive functionality                     informational/cosmetic: no integration impact shown
CHANGE_TYPES = ("deprecation", "breaking", "removal", "schema", "new", "informational", "cosmetic")
SEVERITIES = ("high", "medium", "low")
# Extraction strategies; `html` is kept as an alias of `http` for older configs.
# (`docs_watchdog.extractors.STRATEGIES` is the registry actually used; custom strategies added with
# `register_strategy` become valid `type` values too.)
SOURCE_TYPES = ("http", "html", "markdown", "rss", "openapi", "playwright")


@dataclass
class Source:
    id: str
    type: str
    url: str
    verified: bool = False
    selector: str | None = None  # CSS selector (playwright)
    min_chars: int = 200  # below this the extraction counts as empty
    fallback: Source | None = None  # tried if the primary strategy fails
    # A new extraction shorter than this fraction of the stored baseline is treated as a
    # probably partial page: it is reported as an error and never replaces the baseline.
    # 0 disables the check (use it deliberately, e.g. after a genuine large rewrite).
    min_length_ratio: float = 0.5
    # Connector-specific completeness and cleanup (all optional, all applied by the shared engine):
    expect_text: list[str] = field(default_factory=list)  # each must appear, else "incomplete source"
    drop_sections: list[str] = field(default_factory=list)  # headings whose section is volatile UI/summary text
    ignore_patterns: list[str] = field(default_factory=list)  # regexes; matching whole lines are removed


@dataclass
class Connector:
    name: str
    priority: int
    enabled: bool
    sources: list[Source] = field(default_factory=list)
    id: str = ""  # stable identifier (defaults to a slug of `name`)
    vendor: str = ""
    scope: str = ""  # product/API scope covered, for humans
    example: bool = False  # templates/examples: never counted as a production connector


@dataclass
class Finding:
    connector: str
    source_id: str
    source_url: str
    change_type: ChangeType
    severity: Severity
    affected_area: str
    deadline: str | None
    evidence: str
    recommended_action: str
    summary: str = ""
    impact: str = ""
    confidence: float = 0.5
    confirmed: bool = False  # vendor explicitly states a deprecation/sunset/breaking change
    evidence_verified: bool = True  # evidence found verbatim in the diff
    detected_on: str = ""
    classifier: str = ""  # who produced it, e.g. "deterministic-rules" or "anthropic:<model>"
    old_value: str = ""  # previous state (text or structural fact), when there is one
    new_value: str = ""  # new state
    origin: str = "rules"  # "rules" (deterministic engine) or "ai" (optional enhancement)

    # Backwards-compatible aliases (earlier schema)
    @property
    def url(self) -> str:
        return self.source_url

    @property
    def quote(self) -> str:
        return self.evidence
