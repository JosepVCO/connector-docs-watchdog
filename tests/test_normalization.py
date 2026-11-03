import pytest

from docs_watchdog.extract import normalize


def test_sentence_starting_with_updated_on_is_not_deleted():
    # Regression: this used to normalize to just "."
    text = "Updated on March 3, 2026 the reports endpoint was deprecated and will be removed."
    assert normalize(text) == text


def test_inline_last_updated_text_is_kept():
    assert normalize("Last updated 2026-01-01: field X removed") == "Last updated 2026-01-01: field X removed"
    assert "deprecated" in normalize("Updated on 2026-01-01, the v1 API is deprecated.")


@pytest.mark.parametrize("stamp", [
    "Updated: May 21, 2026", "Last updated: May 3, 2026", "Last updated 2026-05-21",
    "Updated on 21 May 2026", "Last modified: 05/21/2026", "Updated 3 days ago", "Reviewed: Oct 1st, 2026.",
])
def test_standalone_stamp_lines_are_still_removed(stamp):
    assert normalize(f"{stamp}\nreal entry") == "real entry"


def test_ui_noise_removed_but_dates_endpoints_and_warnings_kept():
    text = "Share\nWas this helpful?\nWarning: `GET /v1/reports` is deprecated since 2026-03-03.\nPrint"
    assert normalize(text) == "Warning: `GET /v1/reports` is deprecated since 2026-03-03."
