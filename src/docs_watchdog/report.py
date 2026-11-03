from __future__ import annotations

from datetime import date

from .models import Finding

_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}

# (title, change types, explanation). Every finding lands in exactly one section.
_SECTIONS = [
    ("Potentially breaking changes", ("breaking",),
     "Changes that could make existing calls or parsers fail."),
    ("Removals", ("removal",), "Operations, fields or documentation that no longer exist."),
    ("Deprecations", ("deprecation",), "Features announced or flagged for retirement."),
    ("Parameter and schema changes", ("schema",),
     "Parameters, request bodies, responses, fields and types that changed without being breaking."),
    ("New functionality", ("new",), "Additions that do not alter existing behaviour."),
    ("Informational changes", ("informational", "cosmetic"),
     "Changes with no integration impact shown by the evidence."),
]


def _quote(text: str) -> str:
    return "\n".join(f"  > {l}" for l in text.splitlines()) or "  > (none)"


def _sort_key(f: Finding):
    return (_ORDER[f.severity], f.deadline or "9999", f.connector, f.source_id, f.affected_area,
            f.change_type, f.evidence)


def _deadline_text(deadline: str | None, today: date) -> str:
    """The stated date, with its position relative to the report date made explicit."""
    if not deadline:
        return "none stated"
    try:
        days = (date.fromisoformat(deadline) - today).days
    except ValueError:
        return deadline
    if days < 0:
        return (f"{deadline} — **ALREADY PASSED** ({-days} day{'s' if days != -1 else ''} before this "
                "report). The change is probably already in effect: verify current behaviour.")
    if days == 0:
        return f"{deadline} — **today**"
    return f"{deadline} — in {days} day{'s' if days != 1 else ''}"


def _finding(f: Finding, today: date) -> list[str]:
    origin = " · AI interpretation" if f.origin == "ai" else ""
    lines = [
        f"### [{f.severity.upper()}] {f.connector} — {f.change_type}: {f.affected_area}",
        f"- **Provider / product:** {f.connector} · {f.affected_area or 'n/a'}",
        f"- **Detected:** {f.detected_on or today}",
        f"- **Status:** {'Confirmed by source text' if f.confirmed else 'Unconfirmed / inferred'}"
        f" · confidence {f.confidence:.0%} · classifier `{f.classifier or 'n/a'}`{origin}",
        f"- **Deadline:** {_deadline_text(f.deadline, today)}",
        f"- **Description:** {f.summary or f.affected_area}",
        f"- **Potential impact:** {f.impact or 'Not determined from the available evidence.'}",
        f"- **Recommended action:** {f.recommended_action or 'n/a'}",
        f"- **Source:** {f.source_url} (`{f.source_id}`)",
    ]
    if f.old_value:
        lines += ["- **Previous state:**", _quote(f.old_value)]
    if f.new_value and f.new_value != f.evidence:
        lines += ["- **New state:**", _quote(f.new_value)]
    lines += ["- **Evidence:**" + ("" if f.evidence_verified else " ⚠ not found verbatim in source"),
              _quote(f.evidence), ""]
    return lines


_OUTCOME = {
    "baseline_created": "baseline created", "no_changes": "no changes", "changed": "changed",
    "fetch_failed": "**FETCH FAILED**", "extraction_failed": "**EXTRACTION FAILED**",
    "incomplete_source": "**INCOMPLETE SOURCE**", "classification_failed": "**AI STEP FAILED**",
    "": "not processed",
}
_ORIGIN = {
    "live": "**Data origin: LIVE** — content was fetched over the network from the sources listed "
            "below, at the times shown. Nothing in this report comes from fixtures or demo data.",
    "demo": "**Data origin: DEMO** — bundled sample files, no network access. All sample content is "
            "synthetic and illustrative. This is not a monitoring result for any real vendor.",
    "fixture": "**Data origin: FIXTURE** — recorded pages used for testing, not a live fetch.",
}


def _provenance(runs) -> list[str]:
    lines = ["## Source provenance",
             "| Source | Outcome | URL | HTTP | Fetched at (UTC) | Extracted | Fingerprint |",
             "|---|---|---|---|---|---|---|"]
    for r in runs:
        url = r.final_url if r.final_url and r.final_url != r.url else r.url
        redirected = " (redirected)" if r.method != "bundled-file" and r.final_url and r.final_url != r.url else ""
        via = {"browser": " via browser", "bundled-file": " (bundled file)"}.get(r.method, "")
        via += " (fallback)" if r.used_fallback else ""
        lines.append(
            f"| {r.connector} / `{r.source_id}` | {_OUTCOME.get(r.outcome, r.outcome)} | {url}{redirected}{via} "
            f"| {r.http_status if r.http_status is not None else '–'} | {r.fetched_at or '–'} "
            f"| {f'{r.chars:,} chars' if r.chars else '–'} | {f'`{r.fingerprint[:12]}`' if r.fingerprint else '–'} |")
    failed = [r for r in runs if r.detail and r.outcome not in ("changed", "no_changes", "baseline_created")]
    if failed:
        lines += ["", "Failures:", *[f"- {r.connector} / `{r.source_id}`: {_OUTCOME.get(r.outcome, r.outcome)} — {r.detail}"
                                     for r in failed]]
    lines += ["", "_The fingerprint is the SHA-256 of the extracted text (first 12 hex digits), so two runs "
              "can be compared without storing page content._", ""]
    return lines


def render(
    findings: list[Finding],
    errors: list[str] | None = None,
    status: list[str] | None = None,
    classifier: str | None = None,
    demo: bool = False,
    warnings: list[str] | None = None,
    unverified: list[str] | None = None,
    sources_total: int | None = None,
    sources_ok: int | None = None,
    data_origin: str | None = None,
    source_runs: list | None = None,
    today: date | None = None,
) -> str:
    today = today or date.today()
    findings = sorted(findings, key=_sort_key)
    errors, warnings, unverified = errors or [], warnings or [], unverified or []
    lines = [f"# Connector Docs Watchdog report — {today}", ""]
    lines += [_ORIGIN.get(data_origin or "", "**Data origin: not stated.**"), ""]
    if demo:
        lines += ["> **DEMONSTRATION MODE — not AI output.** Findings come from the deterministic",
                  "> rule engine (`deterministic-rules`) over bundled sample data, to show the",
                  "> workflow offline without credentials.", ""]
    if errors:
        lines += [f"**Run status: INCOMPLETE — {len(errors)} problem(s).** Results below cover only "
                  "what could be processed; see *Errors*. Do not read missing findings as "
                  "\"nothing changed\".", ""]
    elif warnings:
        lines += [f"**Run status: COMPLETE, with {len(warnings)} warning(s).**", ""]
    else:
        lines += ["**Run status: COMPLETE.**", ""]
    mode = "AI-enhanced (deterministic findings + AI interpretation)" if classifier else \
        "deterministic (no AI)"
    lines.append(f"_Mode: {mode}" + (f" · AI classifier: `{classifier}`" if classifier else "") + "_")
    if sources_total is not None:
        lines.append(f"_Sources: {sources_ok if sources_ok is not None else '?'} of {sources_total} "
                     "checked and fully processed_")
    lines.append("")
    if source_runs:
        lines += _provenance(source_runs)
    if status:
        lines += ["## Run summary", *[f"- {s}" for s in status], ""]
    if not findings:
        if errors:
            lines.append("No findings could be produced for the sources that were processed "
                         "(see errors below).\n")
        elif source_runs and all(r.outcome == "baseline_created" for r in source_runs):
            lines.append(f"Baselines were created for {len(source_runs)} source(s). Nothing could be "
                         "compared yet: change detection starts with the next run.\n")
        else:
            new_baselines = sum(1 for r in (source_runs or []) if r.outcome == "baseline_created")
            if new_baselines:
                compared = (sources_total or 0) - new_baselines
                lines.append(f"No relevant changes detected in the {compared} source(s) that had a baseline to "
                             f"compare against. {new_baselines} source(s) had no baseline yet, so one was "
                             "created and they are not compared until the next run.\n")
            else:
                checked = f"All {sources_total} source(s) were checked successfully. " \
                    if sources_total else ""
                lines.append(f"No relevant changes detected. {checked}The review completed correctly.\n")
    for title, types, blurb in _SECTIONS:
        group = [f for f in findings if f.change_type in types]
        if group:
            lines += [f"## {title} ({len(group)})", f"_{blurb}_", ""]
            for f in group:
                lines += _finding(f, today)
    if errors:
        lines += ["## Errors (previous snapshots preserved)", *[f"- {e}" for e in errors], ""]
    if warnings:
        lines += ["## Warnings", *[f"- {w}" for w in warnings], ""]
    if findings:
        lines += ["---", "_**How to read severity and confidence.** They come from fixed keyword rules "
                  "(for example: a stated date raises severity; confidence is a constant per rule), "
                  "not from an analysis of your integration. They say how explicit the vendor wording "
                  "is, not how much a particular integration is affected._", "",
                  "_Evidence quotes show that the wording exists in the retrieved change. They do "
                  "not prove that the classification or the stated impact is correct; confirm "
                  "against the source before acting._", ""]
    if unverified:
        lines += ["## Sources not verified",
                  "_These sources are enabled but their configuration says `verified: false`; "
                  "their results should be treated with caution._", *[f"- {u}" for u in unverified], ""]
    return "\n".join(lines) + "\n"
