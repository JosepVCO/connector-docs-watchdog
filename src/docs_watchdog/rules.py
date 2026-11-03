"""Deterministic classification: turns `Change`s into `Finding`s with fixed rules.

No network, no model, no credentials. The same changes always give the same findings. It never
guesses an impact: when the evidence does not show one, the finding says so explicitly.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from .diff import Change, render_change
from .models import Finding

CLASSIFIER = "deterministic-rules"

_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
          r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_MONTH_NUM = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE = re.compile(
    rf"\b(?:(?P<iso>\d{{4}}-\d{{2}}-\d{{2}})"
    rf"|(?P<m1>{_MONTH})\.? (?P<d1>\d{{1,2}}), (?P<y1>\d{{4}})"  # May 19, 2026 / Oct 15, 2026
    rf"|(?P<d2>\d{{1,2}}) (?P<m2>{_MONTH})\.? (?P<y2>\d{{4}}))\b"  # 19 May 2026
)
_DEPRECATION = re.compile(r"\b(deprecat\w*|sunset\w*|will be retired|end of life|end-of-life|"
                          r"available until|discontinu\w*)\b", re.I)
_REMOVAL = re.compile(r"\b(removed|removal|no longer (?:supported|available|work\w*))\b", re.I)
_BREAKING = re.compile(r"\b(breaking changes?|will stop working|incompatible change|action required)\b", re.I)
_API_REF = re.compile(r"(`[^`]+`|\b(?:GET|POST|PUT|PATCH|DELETE)\s+/|/v\d+[./])")

# A date is a deadline candidate when retirement wording precedes it in the same sentence.
_CUE = re.compile(r"(sunset|removed|removal|retire\w*|deprecat\w*|discontinu\w*|ends?|until|expir\w*|"
                  r"\bby\b|effective|no longer|stops?|shut\w*)[^.!?]{0,60}$", re.I)

_MIN_LINE = 12  # shorter lines are headings/labels, not statements
_MAX_SHOWN = 10  # informational blocks listed per finding (the rest is counted, not hidden)
_MAX_BLOCK = 800

_ACTION = "Review the vendor notice and check whether any connector uses the affected feature."
_NO_IMPACT = ("Not determined: the evidence shows the change but not which integrations use it. "
              "Check the affected area against your connector's usage.")


def _to_iso(match: re.Match) -> str | None:
    try:
        if match.group("iso"):
            return datetime.strptime(match.group("iso"), "%Y-%m-%d").date().isoformat()
        g = match.groupdict()
        month, day, year = (g["m1"], g["d1"], g["y1"]) if g["m1"] else (g["m2"], g["d2"], g["y2"])
        return date(int(year), _MONTH_NUM[month[:3].lower()], int(day)).isoformat()
    except (ValueError, KeyError):
        return None


def _deadline(evidence: str) -> tuple[str | None, int]:
    """-> (deadline or None, number of distinct dates). One date is the deadline; with several, the
    only one introduced by retirement wording is; otherwise none is guessed."""
    found = [(m, _to_iso(m)) for m in _DATE.finditer(evidence)]
    dates = sorted({iso for _, iso in found if iso})
    if len(dates) <= 1:
        return (dates[0] if dates else None), len(dates)
    cued = sorted({iso for m, iso in found if iso and _CUE.search(evidence[max(0, m.start() - 80):m.start()])})
    return (cued[0] if len(cued) == 1 else None), len(dates)


_NEGATION = re.compile(r"\b(no|not|never|without|none)\b|n't\b", re.I)


def _negated(line: str, start: int) -> bool:
    """True when the keyword at `start` is negated in its own clause ("don't contain breaking
    changes", "no breaking changes", "is not deprecated"). Only the ~30 characters before it in the
    same clause count, so "Do not use X; it will be removed" is still a hit."""
    clause = re.split(r"[.;:!?]", line[:start])[-1]
    return bool(_NEGATION.search(clause[-30:]))


def _signal(line: str):
    """-> (change_type, matched keyword) or None. Negated mentions are not signals."""
    for rx, kind in ((_DEPRECATION, "deprecation"), (_REMOVAL, "removal"), (_BREAKING, "breaking")):
        for m in rx.finditer(line):
            if not _negated(line, m.start()):
                return kind, m.group(0).lower()
    return None


def _text_hits(lines: list[str]):
    """Yield (line_index, kind, keyword, evidence_lines) following a one-line-lookahead window:
    the evidence is the matching line, plus the next one when that is where the date is."""
    covered = -1
    for i, line in enumerate(lines):
        if i <= covered or len(line) < _MIN_LINE:
            continue
        sig = _signal(line)
        if not sig:
            continue
        window = lines[i : i + 1]
        if not _DATE.search(line) and i + 1 < len(lines) and _DATE.search(lines[i + 1]):
            window = lines[i : i + 2]
        covered = i + len(window) - 1
        yield i, sig[0], sig[1], window


_TITLES = {"deprecation": "Deprecation", "removal": "Removal", "breaking": "Breaking-change"}


def _make(conn, source_id, url, today, **kw) -> Finding:
    kw.setdefault("classifier", CLASSIFIER)
    kw.setdefault("origin", "rules")
    return Finding(connector=conn, source_id=source_id, source_url=url, detected_on=today,
                   evidence_verified=True, **kw)


def _clip(text: str, limit: int = _MAX_BLOCK) -> str:
    return text if len(text) <= limit else text[:limit] + " […]"


_HEADING_LINE = re.compile(r"^#{1,6}\s+\S")


def _segments(lines: list[str], context: str) -> list[tuple[str, list[str]]]:
    """Split lines into heading-delimited sections: [(heading, lines)]. Text before the first
    heading belongs to `context` (the heading the change sits under)."""
    segs: list[tuple[str, list[str]]] = [(context, [])]
    for line in lines:
        if _HEADING_LINE.match(line):
            segs.append((line.lstrip("#").strip(), [line]))
        else:
            segs[-1][1].append(line)
    return [s for s in segs if s[1]]


def _segment_findings(heading, lines, ch, old_sig, conn, sid, url, today) -> list[Finding]:
    """One finding per signal kind in a section (not one per matching sentence)."""
    by_kind: dict[str, dict] = {}
    for _, kind, kw, window in _text_hits(lines):
        g = by_kind.setdefault(kind, {"kws": [], "windows": []})
        g["kws"].append(kw)
        g["windows"].append(window)
    out = []
    for kind, g in by_kind.items():
        evidence = "\n".join(dict.fromkeys(l for w in g["windows"] for l in w))
        deadline, n_dates = _deadline(evidence)
        first = _DATE.search(evidence)
        if old_sig and old_sig[0] and old_sig[0][0] == kind and old_sig[1] == (first[0] if first else None):
            continue  # same signal as before: wording edit, not a new announcement
        where = "modified" if ch.kind == "modified" else "newly added"
        kws = ", ".join(repr(k) for k in dict.fromkeys(g["kws"]))
        note = f" ({n_dates} different dates appear in the evidence; none is singled out as the deadline)" \
            if n_dates > 1 and not deadline else ""
        out.append(_make(
            conn, sid, url, today,
            change_type=kind, severity="high" if deadline else "medium",
            affected_area=(heading or g["windows"][0][0].lstrip("# "))[:80],
            deadline=deadline, evidence=evidence, confirmed=bool(deadline), confidence=0.6,
            summary=f"{_TITLES[kind]} language ({kws}) in {where} documentation text{note}.",
            impact="Keyword-based detection. " + _NO_IMPACT,
            recommended_action=_ACTION,
            old_value=_clip(ch.old, 1500) if ch.kind == "modified" else "",
            new_value=_clip("\n".join(lines), 1500),
        ))
    return out


def _text_findings(changes: list[Change], conn, sid, url, today) -> list[Finding]:
    out: list[Finding] = []
    info_added: list[str] = []
    info_removed: list[str] = []
    info_modified: list[Change] = []
    removed_api = False
    for ch in changes:
        if ch.kind in ("added", "modified"):
            lines = [l.strip() for l in ch.new.splitlines() if l.strip()]
            old_sig = None
            if ch.kind == "modified":
                old_date = _DATE.search(ch.old)
                old_sig = (_signal(ch.old), old_date[0] if old_date else None)
            if ch.kind == "modified":
                found = _segment_findings(ch.context, lines, ch, old_sig, conn, sid, url, today)
                out += found
                if not found:
                    info_modified.append(ch)
                continue
            for heading, seg in _segments(lines, ch.context):
                found = _segment_findings(heading, seg, ch, None, conn, sid, url, today)
                out += found
                if not found and not (len(seg) == 1 and _HEADING_LINE.match(seg[0])):
                    info_added.append("\n".join(seg))  # a bare new heading alone is not worth a line
        else:  # removed text
            info_removed.append(ch.old)
            removed_api = removed_api or bool(_API_REF.search(ch.old))

    def aggregate(blocks, change_type, severity, area, summary, impact, old=False):
        shown = "\n---\n".join(_clip(b) for b in blocks[:_MAX_SHOWN])
        if len(blocks) > _MAX_SHOWN:
            shown += f"\n--- (+{len(blocks) - _MAX_SHOWN} more block(s) not shown here; " \
                     "the full text is in the stored snapshots)"
        out.append(_make(conn, sid, url, today, change_type=change_type, severity=severity,
                         affected_area=area, deadline=None, evidence=shown, confirmed=False,
                         confidence=0.6, summary=summary, impact=impact,
                         recommended_action="Skim the changed text for relevance.",
                         old_value=shown if old else "", new_value="" if old else shown))

    if info_added:
        aggregate(info_added, "informational", "low", f"{len(info_added)} documentation section(s) added",
                  "Documentation text was added. No deprecation, removal or breaking-change wording "
                  "was detected.", "Not determined: additions may be new functionality or editorial.")
    if info_modified:
        aggregate([render_change(c) for c in info_modified], "informational", "low",
                  f"{len(info_modified)} documentation line(s) modified",
                  "Documentation lines were rewritten. No deprecation, removal or breaking-change "
                  "wording was detected.", "Not determined: the rewrite may or may not change behaviour.")
        out[-1].old_value = _clip("\n".join(c.old for c in info_modified), 1500)
        out[-1].new_value = _clip("\n".join(c.new for c in info_modified), 1500)
    if info_removed:
        aggregate(info_removed, "removal", "medium" if removed_api else "low",
                  f"{len(info_removed)} documentation block(s) removed",
                  "Documentation text was removed."
                  + (" The removed text mentions API elements (code spans, paths or versions)." if removed_api else ""),
                  "Not determined: the text may describe something removed from the API or may have "
                  "been reorganised. Verify against the vendor page.", old=True)
    return out


# ---- OpenAPI structural findings ----------------------------------------------------------
def _attr_diffs(old: dict, new: dict) -> list[str]:
    return [f"{k}: {old.get(k, '-')} -> {new.get(k, '-')}"
            for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]


def _structural(ch: Change):
    """-> (change_type, severity, summary, impact). Never labels informational changes breaking."""
    what = ch.item or ch.entity
    spec_note = "Derived from the OpenAPI specification only; "
    if ch.facet == "version":
        return "informational", "low", f"API version changed ({ch.old} -> {ch.new}).", \
            "No integration impact is shown by a version number alone."
    if ch.facet == "operation":
        if ch.kind == "added":
            return "new", "low", f"Operation added: {ch.entity}.", \
                "Additive: no existing call is changed by this evidence."
        return "removal", "high", f"Operation removed: {ch.entity}.", \
            "Integrations calling this operation are potentially affected: the specification no longer describes it."
    if ch.facet == "deprecated":
        if ch.new_attrs.get("deprecated"):
            return "deprecation", "medium", f"Operation marked deprecated: {ch.entity}.", \
                "The specification flags it deprecated but states no removal date."
        return "informational", "low", f"Deprecation flag removed from {ch.entity}.", \
            "The operation is no longer flagged deprecated."
    if ch.facet == "schema":
        if ch.kind == "added":
            return "new", "low", f"{ch.entity} added.", "Additive: no existing schema is changed."
        return "breaking", "medium", f"{ch.entity} removed.", \
            spec_note + "any request or response using this schema is potentially affected."
    if ch.facet in ("operationId", "summary"):
        return "informational", "low", f"{ch.facet} changed on {ch.entity}.", \
            "Cosmetic for HTTP callers; may rename generated SDK methods (operationId)."
    diffs = _attr_diffs(ch.old_attrs, ch.new_attrs)
    detail = "; ".join(diffs)
    req_old, req_new = ch.old_attrs.get("required"), ch.new_attrs.get("required")
    typ_changed = ch.old_attrs.get("type") != ch.new_attrs.get("type")
    label = {"param": "Parameter", "body": "Request body", "response": "Response",
             "field": "Schema field", "type": "Schema type"}.get(ch.facet, ch.facet)
    where = f"{label} '{what.split(' ', 1)[-1]}' on {ch.entity}"
    if ch.kind == "added":
        if ch.new_attrs.get("required") == "true" and ch.facet in ("param", "body", "field"):
            return "breaking", "high" if ch.facet != "field" else "medium", \
                f"Required {label.lower()} added: {where}.", \
                "Clients that do not send it can fail" + (" (if this is a request schema)." if ch.facet == "field" else ".")
        return "schema", "low", f"{label} added: {where}.", "Additive: existing calls are not changed by this evidence."
    if ch.kind == "removed":
        if ch.facet == "response" or ch.facet == "field":
            return "breaking", "medium", f"{label} removed: {where}.", \
                spec_note + "consumers that read it are potentially affected."
        return "schema", "medium", f"{label} removed: {where}.", \
            spec_note + "whether clients still sending it are ignored or rejected is not stated."
    # modified
    if req_old == "false" and req_new == "true":
        return "breaking", "high" if ch.facet != "field" else "medium", \
            f"{label} became required: {where} ({detail}).", "Clients that omit it can fail."
    if typ_changed:
        return "breaking", "medium", f"Type changed: {where} ({detail}).", \
            spec_note + "clients relying on the previous type are potentially affected."
    if req_old == "true" and req_new == "false":
        return "schema", "low", f"{label} no longer required: {where}.", \
            "Relaxation: existing calls keep working."
    return "schema", "low", f"{label} changed: {where} ({detail}).", \
        "Not determined from the specification."


def _structural_findings(changes: list[Change], conn, sid, url, today) -> list[Finding]:
    out = []
    for ch in changes:
        ctype, sev, summary, impact = _structural(ch)
        out.append(_make(
            conn, sid, url, today, change_type=ctype, severity=sev,
            affected_area=(ch.entity + (f" · {ch.item}" if ch.item else ""))[:120],
            deadline=None, evidence=render_change(ch), confirmed=True, confidence=0.9,  # stated by the spec itself
            summary=summary, impact=impact,
            recommended_action=("Check connector usage of this element and plan the change."
                                if ctype in ("breaking", "removal", "deprecation") else
                                "No action required unless a connector needs the new element."),
            old_value=ch.old, new_value=ch.new,
        ))
    return out


def classify_changes(changes: list[Change], connector: str, source_id: str, url: str,
                     today: date | None = None) -> list[Finding]:
    """Deterministic findings for a source's changes. Order is stable."""
    day = (today or date.today()).isoformat()
    structural = [c for c in changes if c.domain == "openapi"]
    text = [c for c in changes if c.domain != "openapi"]
    return _structural_findings(structural, connector, source_id, url, day) + \
        _text_findings(text, connector, source_id, url, day)
