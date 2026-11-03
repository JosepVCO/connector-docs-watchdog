from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Snapshots are only ever written for successful, non-empty extractions."""

    def __init__(self, path: Path):
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS snapshots (
                 connector TEXT, source_id TEXT, source_url TEXT,
                 fetched_at TEXT, hash TEXT, content TEXT)"""
        )
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(snapshots)")}
        if "source_url" not in cols:  # migrate DBs from the first MVP
            self.db.execute("ALTER TABLE snapshots ADD COLUMN source_url TEXT")

    def latest(self, connector: str, source_id: str) -> str | None:
        row = self.db.execute(
            "SELECT content FROM snapshots WHERE connector=? AND source_id=? "
            "ORDER BY rowid DESC LIMIT 1",
            (connector, source_id),
        ).fetchone()
        return row[0] if row else None

    def save(self, connector: str, source_id: str, content: str, source_url: str = "") -> bool:
        """Insert a snapshot; returns False (and writes nothing) if empty or unchanged."""
        if not content.strip():
            raise ValueError("refusing to store an empty snapshot")
        if self.latest(connector, source_id) == content:
            return False
        h = hashlib.sha256(content.encode()).hexdigest()
        self.db.execute(
            "INSERT INTO snapshots (connector, source_id, source_url, fetched_at, hash, content) "
            "VALUES (?,?,?,?,?,?)",
            (connector, source_id, source_url, utcnow(), h, content),
        )
        self.db.commit()
        return True


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unnamed"


class FileStore:
    """Plain-text snapshots, one file per source: `<root>/snapshots/<connector>/<source>.txt`
    plus a small `.json` with URL, timestamp and hash. Meant to live in a Git branch: Git history
    then keeps every previous snapshot, diffs are readable, and nothing is ever overwritten
    without a trace. Same interface and rules as `Store`."""

    def __init__(self, root: Path):
        self.root = Path(root) / "snapshots"
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, connector: str, source_id: str) -> tuple[Path, Path]:
        d = self.root / _slug(connector)
        return d / f"{_slug(source_id)}.txt", d / f"{_slug(source_id)}.json"

    def latest(self, connector: str, source_id: str) -> str | None:
        txt, _ = self._paths(connector, source_id)
        return txt.read_text(encoding="utf-8") if txt.exists() else None

    @staticmethod
    def _atomic_write(path: Path, data: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)

    def save(self, connector: str, source_id: str, content: str, source_url: str = "") -> bool:
        if not content.strip():
            raise ValueError("refusing to store an empty snapshot")
        if self.latest(connector, source_id) == content:
            return False
        txt, meta = self._paths(connector, source_id)
        txt.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(txt, content)
        self._atomic_write(meta, json.dumps({
            "connector": connector, "source_id": source_id, "source_url": source_url,
            "fetched_at": utcnow(), "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "chars": len(content)}, indent=2, sort_keys=True) + "\n")
        return True
