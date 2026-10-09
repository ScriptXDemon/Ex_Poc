"""SQLite response cache keyed by a hash of the full request (model, provider prefs, messages, schema, params)."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class ResponseCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT NOT NULL, created REAL)")
        self._conn.commit()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT v FROM cache WHERE k = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        import time

        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO cache (k, v, created) VALUES (?, ?, ?)",
                               (key, json.dumps(value), time.time()))
            self._conn.commit()
