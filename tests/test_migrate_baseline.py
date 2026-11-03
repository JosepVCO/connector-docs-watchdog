import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("migrate_baseline", ROOT / "scripts/migrate_baseline.py")
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)

CFG = """connectors:
  - name: LinkedIn Marketing
    sources: [{id: recent, type: http, verified: true, url: 'https://x/r',
               ignore_patterns: ['Access to this page.*'], drop_sections: ['Page Summary']}]
"""
OLD = ("Access to this page requires authorization.\\n## Page Summary\\n- auto summary\\n"
       "## Real\\nreal entry line").replace("\\n", "\n")


def make_old_db(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE snapshots (connector TEXT, source_id TEXT, source_url TEXT, "
               "fetched_at TEXT, hash TEXT, content TEXT)")
    db.execute("INSERT INTO snapshots VALUES ('LinkedIn Ads','recent','https://x/r','2026-11-03T13:45:50+00:00',"
               "?,?)", (hashlib.sha256(OLD.encode()).hexdigest(), OLD))
    db.execute("INSERT INTO snapshots VALUES ('Gone Ads','x','u','t','h','c')")  # not in config
    db.commit()
    db.close()


def test_migration_renames_cleans_preserves_metadata_and_never_touches_the_source(tmp_path):
    src, dst, cfg = tmp_path / "old.db", tmp_path / "new.db", tmp_path / "c.yaml"
    make_old_db(src)
    cfg.write_text(CFG)
    before = src.read_bytes()
    rows = mb.migrate(src, dst, cfg, {"LinkedIn Ads": "LinkedIn Marketing"})
    assert src.read_bytes() == before  # source untouched
    assert len(rows) == 1 and rows[0][:3] == ("LinkedIn Ads", "LinkedIn Marketing", "recent")
    out = sqlite3.connect(dst).execute("select connector, fetched_at, content, hash from snapshots").fetchall()
    assert len(out) == 1  # the connector that no longer exists is skipped, not silently kept
    name, fetched_at, content, h = out[0]
    assert name == "LinkedIn Marketing" and fetched_at == "2026-11-03T13:45:50+00:00"  # history kept
    assert content == "## Real\nreal entry line" and h == hashlib.sha256(content.encode()).hexdigest()


def test_migration_refuses_to_overwrite_an_existing_destination(tmp_path):
    src, dst, cfg = tmp_path / "old.db", tmp_path / "new.db", tmp_path / "c.yaml"
    make_old_db(src)
    cfg.write_text(CFG)
    dst.write_text("precious")
    with pytest.raises(SystemExit):
        mb.migrate(src, dst, cfg, {})
    assert dst.read_text() == "precious"
