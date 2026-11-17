from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Store:
    """SQLite event log for relay state changes."""

    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_events (
                    id     INTEGER PRIMARY KEY AUTOINCREMENT,
                    relay  INTEGER NOT NULL,
                    state  INTEGER NOT NULL,
                    ts     INTEGER NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_events_ts "
                "ON relay_events (ts)"
            )
            self._conn.commit()

    def record(self, relay: int, state: bool, ts: int | None = None) -> dict[str, Any]:
        ts = int(ts if ts is not None else time.time())
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO relay_events (relay, state, ts) VALUES (?, ?, ?)",
                (relay, 1 if state else 0, ts),
            )
            self._conn.commit()
            event_id = cursor.lastrowid
        return {"id": event_id, "relay": relay, "state": bool(state), "ts": ts}

    def history(
        self,
        relay: int | None = None,
        since: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        query = "SELECT id, relay, state, ts FROM relay_events"
        clauses: list[str] = []
        params: list[Any] = []

        if relay is not None:
            clauses.append("relay = ?")
            params.append(relay)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        query += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(int(limit))

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        return [
            {
                "id": row["id"],
                "relay": row["relay"],
                "state": bool(row["state"]),
                "ts": row["ts"],
            }
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
