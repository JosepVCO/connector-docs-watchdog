import pytest

from docs_watchdog.diff import make_diff
from docs_watchdog.extract import normalize

BASE = "## 05/04/2026\nAccess tier renamed.\n## 02/18/2026\nv25 released."


def test_new_entry_detected():
    new = "## 11/01/2026\nField foo removed.\n" + BASE
    assert make_diff(BASE, new) == "## 11/01/2026\nField foo removed."


def test_modified_entry_detected():
    new = BASE.replace("v25 released.", "v25 released. Available until Feb 2027.")
    assert make_diff(BASE, new) == "v25 released. Available until Feb 2027."


def test_duplicate_or_moved_content_not_flagged():
    moved = "## 02/18/2026\nv25 released.\n## 05/04/2026\nAccess tier renamed."
    assert make_diff(BASE, moved) == ""


def test_no_changes():
    assert make_diff(BASE, BASE) == ""


def test_volatile_noise_is_normalized_away():
    a = normalize("Updated: May 21, 2026\nWas this helpful?\nCopy for LLM\nreal entry")
    b = normalize("Updated: Oct 19, 2026\nCopy for LLM\nWas this helpful?\nreal entry")
    assert a == b == "real entry"


def test_normalization_keeps_changelog_content():
    text = "05/04/2026\nUpdated field `status`; v17 updated on the server side is unaffected"
    assert "v17" in normalize(text) and "05/04/2026" in normalize(text)


def test_store_never_persists_empty(store):
    with pytest.raises(ValueError):
        store.save("c", "s", "   ")
    assert store.latest("c", "s") is None


def test_store_dedupes_identical_snapshot(store):
    assert store.save("c", "s", "abc", "http://u") is True
    assert store.save("c", "s", "abc", "http://u") is False
    assert store.db.execute("select count(*) from snapshots").fetchone()[0] == 1
    assert store.db.execute("select source_url, fetched_at from snapshots").fetchone()[0] == "http://u"
