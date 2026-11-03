from __future__ import annotations

import json
import logging
import re
from datetime import date

from .llm import AnthropicProvider, LLMProvider, LLMTruncatedError
from .models import CHANGE_TYPES, SEVERITIES, Finding
from .rules import _DATE, _to_iso

log = logging.getLogger("docs_watchdog.classify")

SYSTEM = """You triage vendor API documentation changes for a data-integration company
whose connectors call that vendor's API (extracting reports, metrics, dimensions, fields).
You receive the changes detected in a vendor's documentation or OpenAPI specification as labelled
blocks: [ADDED], [REMOVED], [MODIFIED] (- old / + new) and [OPENAPI ...] structural facts.
REMOVED and MODIFIED content matters as much as added content. List changes that could affect
such connectors.
Return ONLY JSON: {"findings": [{"change_type": "deprecation|breaking|removal|schema|new|informational",
"severity": "high|medium|low", "summary": str, "impact": str, "affected_area": str,
"confirmed": bool, "deadline": "YYYY-MM-DD or null", "evidence": "verbatim excerpt from the text",
"recommended_action": str, "confidence": number between 0 and 1}]}.
Rules:
- evidence MUST be copied verbatim from the provided text.
- confirmed=true only if the text itself explicitly states the deprecation/sunset/breaking change;
  if you are inferring it, set confirmed=false and say so in the summary.
- deadline only if a date is stated in the evidence; never infer or invent dates; otherwise null.
- Ordinary documentation changes and additive features are change_type "new" / low severity.
- Skip purely cosmetic or irrelevant items; return an empty list if nothing matters.
- Do not call a change breaking unless the evidence shows an incompatibility; additive changes are
  "new"/"schema" with low severity.
- high = confirmed removal/sunset or breaking change to endpoints, fields, auth or versions."""


class ClassificationError(Exception):
    """The LLM call failed or returned an unusable response."""


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _valid_date(s: str | None) -> bool:
    if not s or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return False
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


def _deadline_stated(deadline: str, evidence: str) -> bool:
    """The FULL date must be written in the evidence (ISO, 'May 19, 2026' or '19 May 2026');
    a matching year alone proves nothing."""
    return any(_to_iso(m) == deadline for m in _DATE.finditer(evidence))


def parse_findings(
    payload: str, connector: str, source_id: str, url: str, source_text: str | None = None,
    classifier: str = "", rejected: list[str] | None = None, origin: str = "ai",
) -> list[Finding]:
    """Validate a model response. Findings that fail validation are not silently lost: each one
    is described in `rejected` (when given) so the caller can surface it."""
    start, end = payload.find("{"), payload.rfind("}")
    if start < 0 or end < start:
        raise ClassificationError("response contains no JSON object")
    try:
        data = json.loads(payload[start : end + 1])
        items = data["findings"]
        if not isinstance(items, list):
            raise TypeError("findings is not a list")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise ClassificationError(f"malformed JSON response: {e}") from e

    haystack = _squash(source_text) if source_text is not None else None
    today = date.today().isoformat()
    out: list[Finding] = []
    for f in items:
        if not isinstance(f, dict) or f.get("change_type") not in CHANGE_TYPES \
                or f.get("severity") not in SEVERITIES:
            log.warning("%s/%s: dropping finding with invalid type/severity: %r", connector,
                        source_id, f)
            if rejected is not None:
                rejected.append(f"invalid change_type/severity: {str(f)[:200]}")
            continue
        evidence = f.get("evidence") or f.get("quote") or ""
        verified = bool(evidence) and (haystack is None or _squash(evidence) in haystack)
        if not verified:
            log.warning("%s/%s: evidence not found verbatim in source diff", connector, source_id)
        deadline = f.get("deadline")
        if not (verified and _valid_date(deadline) and _deadline_stated(deadline, evidence)):
            if deadline:
                log.warning("%s/%s: discarding unsupported deadline %r", connector, source_id,
                            deadline)
            deadline = None
        confirmed = f.get("confirmed", False)  # only an explicit boolean true can confirm
        if not isinstance(confirmed, bool):
            if rejected is not None:
                rejected.append(f"'confirmed' must be a boolean, got {confirmed!r}: treated as false")
            confirmed = False
        severity = f["severity"]
        if severity == "high" and not confirmed:
            severity = "medium"  # unconfirmed assumptions never rank as top severity
        try:
            confidence = max(0.0, min(1.0, float(f.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        if not verified:
            confidence = min(confidence, 0.5)
        out.append(
            Finding(
                connector=connector,
                source_id=source_id,
                source_url=url,
                change_type=f["change_type"],
                severity=severity,
                affected_area=f.get("affected_area", ""),
                deadline=deadline,
                evidence=evidence,
                recommended_action=f.get("recommended_action", ""),
                summary=f.get("summary", ""),
                impact=f.get("impact", ""),
                confidence=confidence,
                confirmed=confirmed and verified,
                evidence_verified=verified,
                detected_on=today,
                classifier=classifier,
                origin=origin,
            )
        )
    return out


def _split_text(text: str) -> tuple[str, str] | None:
    """Split at a block boundary near the middle (or at the middle line); None if unsplittable."""
    lines = text.split("\n")
    if len(lines) < 2:
        return None
    mid = len(lines) // 2
    cut = min((i for i in range(1, len(lines)) if lines[i].startswith("[")),
              key=lambda i: abs(i - mid), default=mid)
    return "\n".join(lines[:cut]), "\n".join(lines[cut:])


def classify(
    diff_text: str, connector: str, source_id: str, url: str,
    client=None, provider: LLMProvider | None = None,
    rejected: list[str] | None = None, _depth: int = 0,
) -> list[Finding]:
    """Classify rendered changes with any LLMProvider. `client` (an Anthropic-style SDK client)
    is the legacy injection point and is wrapped into an AnthropicProvider.

    A truncated or unparseable response is retried on two halves of the input (bounded depth);
    if that cannot complete, ClassificationError is raised so the caller never treats a partial
    result as complete."""
    if not diff_text.strip():
        return []
    if provider is None:
        if client is None:
            raise ClassificationError("no LLM provider configured")
        provider = AnthropicProvider(client=client)
    failure: ClassificationError
    try:
        text = provider.complete(SYSTEM, f"Connector: {connector}\n\n{diff_text}")
        return parse_findings(text, connector, source_id, url, source_text=diff_text,
                              classifier=provider.name, rejected=rejected)
    except LLMTruncatedError as e:
        failure = ClassificationError(f"{provider.name} response truncated: {e}")
    except ClassificationError as e:
        failure = e
    except Exception as e:  # SDK/transport errors; never include credentials
        raise ClassificationError(f"{provider.name} call failed: {type(e).__name__}: {e}") from e
    halves = _split_text(diff_text) if _depth < 3 else None
    if halves is None:
        raise failure
    log.warning("%s/%s: %s; retrying on two halves", connector, source_id, failure)
    return (classify(halves[0], connector, source_id, url, provider=provider, rejected=rejected,
                     _depth=_depth + 1)
            + classify(halves[1], connector, source_id, url, provider=provider, rejected=rejected,
                       _depth=_depth + 1))


class AIClassifier:
    """Pipeline hook for the optional AI layer. Collects findings the model produced that failed
    validation in `rejected`, so the run report can show them instead of dropping them."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.rejected: list[str] = []

    def __call__(self, text: str, connector: str, source_id: str, url: str) -> list[Finding]:
        return classify(text, connector, source_id, url, provider=self.provider,
                        rejected=self.rejected)
