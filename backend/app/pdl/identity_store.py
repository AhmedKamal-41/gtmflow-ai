"""Disk-backed identity/content-hash index for curation dedup (Phase 3
closeout Part C.1).

The original implementation kept `seen_identity_hash: dict[str, str]` in a
plain Python dict, which holds one entry per ELIGIBLE record seen (not the
full ~32.3M-row corpus, but still ~962,000 entries on the real PDL scan --
see docs/engineering-log/phase3-data-handoff.md). That's a genuine unbounded-growth
concern in the general case (a future, larger, or less-filtered corpus could
make the eligible population itself huge), so this replaces it with a
temporary on-disk SQLite index, with a small bounded in-memory LRU cache in
front for throughput. The reservoirs themselves (`curate.py`'s `_Reservoir`)
stay in memory -- they're fixed-size by construction (`TARGET_PER_SEGMENT`),
never grow with the corpus, and were never the concern here.
"""
from __future__ import annotations

import sqlite3
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Optional


class IdentityHashStore:
    """Maps identity_key -> content_hash, backed by a temp SQLite file.

    Not thread/process-safe (one writer, this process, for the duration of
    one curate() run) -- matches the existing single-process streaming scan.
    Call close() when done; the temp file is removed then (or leave it, via
    keep_file=True, for inspection/testing).
    """

    def __init__(self, cache_size: int = 50_000, db_path: Optional[Path] = None) -> None:
        self._owns_file = db_path is None
        if db_path is None:
            fd, name = tempfile.mkstemp(prefix="pdl_identity_", suffix=".sqlite3")
            import os

            os.close(fd)
            db_path = Path(name)
        self.path = db_path

        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # fine for a disposable scan-scoped index
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS identity_hash (key TEXT PRIMARY KEY, content_hash TEXT NOT NULL)"
        )
        self._conn.commit()

        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_size = cache_size
        self._pending_writes = 0
        self._commit_every = 2000

    def _cache_get(self, key: str) -> str | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def _cache_put(self, key: str, value: str) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def get(self, key: str) -> str | None:
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        row = self._conn.execute(
            "SELECT content_hash FROM identity_hash WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        self._cache_put(key, row[0])
        return row[0]

    def set(self, key: str, content_hash: str) -> None:
        self._cache_put(key, content_hash)
        self._conn.execute(
            "INSERT OR REPLACE INTO identity_hash (key, content_hash) VALUES (?, ?)",
            (key, content_hash),
        )
        self._pending_writes += 1
        if self._pending_writes >= self._commit_every:
            self._conn.commit()
            self._pending_writes = 0

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM identity_hash").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()
        if self._owns_file:
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(self.path) + suffix)
                if p.exists():
                    p.unlink()

    def __enter__(self) -> "IdentityHashStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
