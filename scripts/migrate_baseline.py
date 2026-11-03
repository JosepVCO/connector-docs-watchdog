"""Copy a snapshot database into a new one, adapting it to the current extraction rules.

Why: when a connector is renamed, or its extraction options change (`drop_sections`,
`ignore_patterns`), old baselines no longer match what a fresh run extracts, and the next run
would report the difference as vendor changes. This script rewrites the stored text exactly the
way a fresh extraction would clean it, so the baseline stays comparable and history is kept.

It NEVER modifies the source database and refuses to overwrite the destination.

    python scripts/migrate_baseline.py data/snapshots.db data/snapshots.migrated.db \\
        --config connectors.yaml --rename "LinkedIn Ads=LinkedIn Marketing"
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

from docs_watchdog.config import load_connectors
from docs_watchdog.extractors import clean
from docs_watchdog.store import Store


def migrate(src: Path, dst: Path, config: Path, renames: dict[str, str]) -> list[tuple]:
    if dst.exists():
        raise SystemExit(f"refusing to overwrite existing {dst}")
    sources = {(c.name, s.id): s for c in load_connectors(config, only_enabled=False) for s in c.sources}
    out = Store(dst)  # creates the current schema
    rows = sqlite3.connect(f"file:{src}?mode=ro", uri=True).execute(
        "SELECT connector, source_id, source_url, fetched_at, hash, content FROM snapshots ORDER BY rowid"
    ).fetchall()
    report = []
    for connector, source_id, url, fetched_at, old_hash, content in rows:
        name = renames.get(connector, connector)
        source = sources.get((name, source_id))
        if source is None:
            print(f"  skipped (no such source in config): {connector}/{source_id}", file=sys.stderr)
            continue
        text = clean(content, source)
        new_hash = hashlib.sha256(text.encode()).hexdigest()
        out.db.execute(
            "INSERT INTO snapshots (connector, source_id, source_url, fetched_at, hash, content) "
            "VALUES (?,?,?,?,?,?)", (name, source_id, url, fetched_at, new_hash, text))
        report.append((connector, name, source_id, len(content), len(text), old_hash[:12], new_hash[:12]))
    out.db.commit()
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--config", type=Path, default=Path("connectors.yaml"))
    ap.add_argument("--rename", action="append", default=[], metavar="OLD=NEW",
                    help="connector rename, repeatable")
    a = ap.parse_args()
    renames = dict(r.split("=", 1) for r in a.rename)
    print(f"{'old connector':<14} {'new connector':<19} {'source':<24} {'chars':>14}  fingerprint")
    for old, new, sid, n_old, n_new, h_old, h_new in migrate(a.src, a.dst, a.config, renames):
        print(f"{old:<14} {new:<19} {sid:<24} {n_old:>6}->{n_new:<6}  {h_old} -> {h_new}")


if __name__ == "__main__":
    main()
