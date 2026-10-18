"""SQLite persistence for sources, chunks and research runs.

All queries are parameterised. Every row is scoped to a workspace id, which the
browser generates at random and keeps in local storage.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .ingest import Chunk, ParsedSource
from .retrieval import Passage

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    workspace TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    chars INTEGER NOT NULL,
    pages INTEGER,
    chunks INTEGER NOT NULL,
    is_sample INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_ws ON sources(workspace, created_at);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    page INTEGER,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id, ord);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    workspace TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at REAL NOT NULL,
    finished_at REAL,
    events TEXT NOT NULL DEFAULT '[]',
    report TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_ws ON runs(workspace, created_at);
"""


def new_id() -> str:
    return uuid.uuid4().hex


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # One shared connection guarded by a lock keeps ":memory:" databases usable in tests.
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        # Runs interrupted by a restart can never finish; mark them so the UI stops waiting.
        self._conn.execute("UPDATE runs SET status='failed', error='Interrupted by a server restart.' WHERE status IN ('queued','running')")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- sources ---------------------------------------------------------

    def count_sources(self, workspace: str) -> int:
        return int(self._query("SELECT COUNT(*) AS n FROM sources WHERE workspace=?", (workspace,))[0]["n"])

    def add_source(self, workspace: str, parsed: ParsedSource, chunks: list[Chunk], origin: str, is_sample: bool = False) -> dict[str, Any]:
        source_id = new_id()
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO sources (id, workspace, title, kind, origin, chars, pages, chunks, is_sample, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    workspace,
                    parsed.title,
                    parsed.kind,
                    origin[:2000],
                    parsed.char_count,
                    parsed.page_count,
                    len(chunks),
                    int(is_sample),
                    now,
                ),
            )
            conn.executemany(
                "INSERT INTO chunks (id, source_id, ord, page, text) VALUES (?,?,?,?,?)",
                [(f"{source_id}:{c.ord}", source_id, c.ord, c.page, c.text) for c in chunks],
            )
        return self.get_source(workspace, source_id) or {}

    def list_sources(self, workspace: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT id, title, kind, origin, chars, pages, chunks, is_sample, created_at FROM sources WHERE workspace=? ORDER BY created_at",
            (workspace,),
        )
        return [dict(r) | {"is_sample": bool(r["is_sample"])} for r in rows]

    def get_source(self, workspace: str, source_id: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT id, title, kind, origin, chars, pages, chunks, is_sample, created_at FROM sources WHERE workspace=? AND id=?",
            (workspace, source_id),
        )
        return (dict(rows[0]) | {"is_sample": bool(rows[0]["is_sample"])}) if rows else None

    def has_sample_sources(self, workspace: str) -> bool:
        return bool(self._query("SELECT 1 FROM sources WHERE workspace=? AND is_sample=1 LIMIT 1", (workspace,)))

    def delete_source(self, workspace: str, source_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM sources WHERE workspace=? AND id=?", (workspace, source_id))
            return cur.rowcount > 0

    def passages(self, workspace: str, source_ids: list[str] | None = None) -> list[Passage]:
        sql = "SELECT c.id, c.source_id, s.title, c.page, c.text FROM chunks c JOIN sources s ON s.id=c.source_id WHERE s.workspace=?"
        params: list[Any] = [workspace]
        if source_ids is not None:
            if not source_ids:
                return []
            sql += f" AND s.id IN ({','.join('?' for _ in source_ids)})"
            params.extend(source_ids)
        sql += " ORDER BY s.created_at, c.ord"
        return [Passage(r["id"], r["source_id"], r["title"], r["page"], r["text"]) for r in self._query(sql, tuple(params))]

    # -- runs ------------------------------------------------------------

    def create_run(self, workspace: str, question: str, mode: str) -> str:
        run_id = new_id()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO runs (id, workspace, question, status, mode, created_at) VALUES (?,?,?,?,?,?)",
                (run_id, workspace, question, "queued", mode, time.time()),
            )
        return run_id

    def active_runs(self, workspace: str) -> int:
        return int(self._query("SELECT COUNT(*) AS n FROM runs WHERE workspace=? AND status IN ('queued','running')", (workspace,))[0]["n"])

    def set_running(self, run_id: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        with self._tx() as conn:
            row = conn.execute("SELECT events FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return
            events = json.loads(row["events"])
            events.append(event)
            conn.execute("UPDATE runs SET events=? WHERE id=?", (json.dumps(events), run_id))

    def finish_run(self, run_id: str, report: dict[str, Any]) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE runs SET status=?, mode=?, report=?, events=?, finished_at=? WHERE id=?",
                (report["status"], report["mode"], json.dumps(report), json.dumps(report["trace"]), time.time(), run_id),
            )

    def fail_run(self, run_id: str, message: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE runs SET status='failed', error=?, finished_at=? WHERE id=?", (message[:500], time.time(), run_id))

    def get_run(self, workspace: str, run_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM runs WHERE workspace=? AND id=?", (workspace, run_id))
        if not rows:
            return None
        r = rows[0]
        return {
            "id": r["id"],
            "question": r["question"],
            "status": r["status"],
            "mode": r["mode"],
            "created_at": r["created_at"],
            "finished_at": r["finished_at"],
            "events": json.loads(r["events"]),
            "report": json.loads(r["report"]) if r["report"] else None,
            "error": r["error"],
        }

    def list_runs(self, workspace: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT id, question, status, mode, created_at, finished_at FROM runs WHERE workspace=? ORDER BY created_at DESC LIMIT ?",
            (workspace, limit),
        )
        return [dict(r) for r in rows]

    def delete_run(self, workspace: str, run_id: str) -> bool:
        with self._tx() as conn:
            return (
                conn.execute(
                    "DELETE FROM runs WHERE workspace=? AND id=? AND status NOT IN ('queued','running')", (workspace, run_id)
                ).rowcount
                > 0
            )

    # -- housekeeping ----------------------------------------------------

    def delete_workspace(self, workspace: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM sources WHERE workspace=?", (workspace,))
            conn.execute("DELETE FROM runs WHERE workspace=? AND status NOT IN ('queued','running')", (workspace,))

    def purge_older_than(self, hours: int) -> int:
        if hours <= 0:
            return 0
        cutoff = time.time() - hours * 3600
        with self._tx() as conn:
            n = conn.execute("DELETE FROM sources WHERE created_at < ?", (cutoff,)).rowcount
            n += conn.execute("DELETE FROM runs WHERE created_at < ? AND status NOT IN ('queued','running')", (cutoff,)).rowcount
        return n
