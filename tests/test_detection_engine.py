from docs_watchdog.diff import (MAX_CHUNK_CHARS, Change, chunk_changes, compute_changes, make_diff,
                           render_changes)
from docs_watchdog.rules import classify_changes

FILLER = "\n".join(f"Release note {i}: general improvements and fixes to reporting." for i in range(400))
SENTINEL = "The v9 reporting endpoint is deprecated and will be removed on 2027-06-30."


def classify(old, new):
    return classify_changes(compute_changes(old, new), "C", "s", "https://x/doc", None)


def test_deprecation_after_char_12000_is_detected_and_chunked_without_loss():
    old = "# Changelog\nintro line that is long enough"
    new = old + "\n" + FILLER + "\n" + SENTINEL
    assert len(FILLER) > 12000
    changes = compute_changes(old, new)
    chunks = chunk_changes(changes, MAX_CHUNK_CHARS)
    assert len(chunks) > 1 and all(len(c) <= MAX_CHUNK_CHARS for c in chunks)
    assert any(SENTINEL in c for c in chunks)  # nothing truncated
    assert sum(c.count("Release note") for c in chunks) == 400  # every added line survives
    findings = classify(old, new)
    dep = [f for f in findings if f.change_type == "deprecation"]
    assert len(dep) == 1 and dep[0].deadline == "2027-06-30"
    assert make_diff(old, new).endswith(SENTINEL)  # legacy helper is no longer capped


def test_single_enormous_line_is_split_not_dropped():
    big = "x" * 30000 + " END-MARK"
    chunks = chunk_changes([Change("added", new=big)], 5000)
    assert all(len(c) <= 5000 for c in chunks)
    assert "".join(c.split("\n", 1)[1] for c in chunks).endswith("END-MARK")
    assert sum(len(c.split("\n", 1)[1]) for c in chunks) == len(big)


def test_removed_text_is_detected():
    old = "# API\nThe `legacy_report` endpoint returns daily totals.\nOther stable line here."
    new = "# API\nOther stable line here."
    ch = compute_changes(old, new)
    assert [(c.kind, c.old) for c in ch] == [("removed", "The `legacy_report` endpoint returns daily totals.")]
    f = classify(old, new)[0]
    assert f.change_type == "removal" and f.severity == "medium"  # mentions an API element
    assert "legacy_report" in f.old_value and "Not determined" in f.impact


def test_removed_plain_prose_is_low_severity():
    f = classify("a\nSome introductory prose about the product.\nb", "a\nb")[0]
    assert f.change_type == "removal" and f.severity == "low"


def test_modified_line_is_paired_and_reports_both_states():
    old = "## Limits\nRequests are limited to 100 per minute per token."
    new = "## Limits\nRequests are limited to 20 per minute per token."
    ch = compute_changes(old, new)
    assert len(ch) == 1 and ch[0].kind == "modified"
    assert "100" in ch[0].old and "20" in ch[0].new
    f = classify(old, new)[0]
    assert f.change_type == "informational" and "100" in f.old_value and "20" in f.new_value


def test_repeated_sentence_for_a_different_endpoint_is_not_lost():
    old = "## Endpoint A\nThis endpoint is deprecated and will be removed on 2027-01-01.\n## Endpoint B\nStable."
    new = old + "\n## Endpoint C\nThis endpoint is deprecated and will be removed on 2027-01-01."
    ch = compute_changes(old, new)
    added = [c for c in ch if c.kind == "added"]
    assert any("deprecated" in c.new and c.context == "Endpoint C" for c in added)
    findings = [f for f in classify(old, new) if f.change_type == "deprecation"]
    assert len(findings) == 1 and findings[0].affected_area == "Endpoint C"


def test_reordered_content_is_not_a_change():
    a = "## One\nfirst entry line\n## Two\nsecond entry line"
    b = "## Two\nsecond entry line\n## One\nfirst entry line"
    assert compute_changes(a, b) == []


def test_modified_deprecation_with_same_signal_is_not_reannounced():
    old = "The v1 API is deprecated and ends on 2027-01-01 for everyone."
    new = "The v1 API is deprecated and ends on 2027-01-01 for everyone, no exceptions."
    kinds = {f.change_type for f in classify(old, new)}
    assert "deprecation" not in kinds  # wording edit, same announcement


def test_changed_deadline_is_reported():
    old = "The v1 API is deprecated and ends on 2027-01-01 for everyone."
    new = "The v1 API is deprecated and ends on 2027-06-30 for everyone."
    f = [f for f in classify(old, new) if f.change_type == "deprecation"][0]
    assert f.deadline == "2027-06-30" and "2027-01-01" in f.old_value


def test_rendering_labels_every_kind():
    ch = compute_changes("keep\ngone line here\nold wording of the limit is 5", "keep\nnew line added\nold wording of the limit is 9")
    text = render_changes(ch)
    assert "[REMOVED" in text or "[MODIFIED" in text
    assert "[ADDED" in text or "[MODIFIED" in text


# ---- negation: found by a controlled simulation on Google's real v24.1 release notes ---------
from docs_watchdog.rules import _signal  # noqa: E402


def test_negated_mentions_are_not_signals():
    for line in ["Minor versions like v24.1 don't contain breaking changes.",
                 "There are no breaking changes in this release.",
                 "This field is not deprecated.",
                 "The upgrade is possible without breaking changes.",
                 "No action required."]:
        assert _signal(line) is None, line


def test_real_signals_survive_the_negation_guard():
    assert _signal("Do not use the v1 endpoint; it will be removed on 2027-01-01.")[0] == "removal"
    assert _signal("Support for call ads is no longer available.")[0] == "removal"
    assert _signal("The beta API is deprecated, not supported anymore.")[0] == "deprecation"
    assert _signal("Breaking changes in v27: the field is gone.")[0] == "breaking"
