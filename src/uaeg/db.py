"""SQLite persistence layer (PRD FR-019, FR-020, section 14).

A single SQLite database stores agent keys (verifiers only), job metadata,
session metadata and execution audit logs. Job *output* is persisted as
files under the data directory (see executor.jobs); this store keeps the
queryable metadata.

Notes:
- WAL journaling for safe concurrent access from the server and the CLI.
- Timestamps are UTC ISO-8601 strings.
- After a reboot, jobs left in 'running' are marked 'interrupted' by the
  JobManager on startup (never falsely reported as running).
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from . import security

T = TypeVar("T")


def _locked(fn: Callable[..., T]) -> Callable[..., T]:
    """Serialize database access across the event loop and worker threads."""
    @functools.wraps(fn)
    def wrapper(self: "Store", *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keys (
    key_id          TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    prefix          TEXT NOT NULL,
    verifier        TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'active',   -- active | revoked
    permission_mode TEXT NOT NULL DEFAULT 'all',      -- all | restricted
    created_at      TEXT NOT NULL,
    last_used_at    TEXT,
    revoked_at      TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    key_id      TEXT,
    key_name    TEXT,
    command     TEXT NOT NULL,
    cwd         TEXT,
    status      TEXT NOT NULL,   -- running|completed|failed|cancelled|interrupted
    pid         INTEGER,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    exit_code   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs(started_at DESC);

CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    key_id        TEXT,
    key_name      TEXT,
    command       TEXT,
    cwd           TEXT,
    status        TEXT NOT NULL,  -- open | closed | interrupted
    pid           INTEGER,
    created_at    TEXT NOT NULL,
    last_activity TEXT,
    closed_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS exec_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    key_id      TEXT,
    key_name    TEXT,
    tool        TEXT NOT NULL,
    meta        TEXT,           -- JSON: command, cwd, session_id, job_id, ...
    status      TEXT NOT NULL,  -- ok | error | timeout | denied
    exit_code   INTEGER,
    duration_ms INTEGER,
    output_preview TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON exec_logs(id DESC);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    """Thin, synchronous SQLite access layer (safe to call from the event
    loop; operations are fast and local)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        self._check_schema_version()
        self._set_meta("schema_version", str(SCHEMA_VERSION))
        try:
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass
        try:
            import os

            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _check_schema_version(self) -> None:
        """Refuse to silently misuse a database from another UAEG version."""
        try:
            row = self._db.execute(
                "SELECT v FROM meta WHERE k='schema_version'"
            ).fetchone()
        except sqlite3.DatabaseError:
            return  # fresh/legacy database without the meta row
        if row is None:
            return
        try:
            stored = int(row["v"])
        except (TypeError, ValueError):
            return
        if stored > SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                f"database schema version {stored} is newer than this UAEG "
                f"supports (schema {SCHEMA_VERSION}); upgrade UAEG first"
            )
        # stored < SCHEMA_VERSION would run migrations here; none exist yet.

    # ------------------------------------------------------------------
    # meta
    # ------------------------------------------------------------------
    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO meta(k, v) VALUES(?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )
        self._db.commit()

    @_locked
    def get_meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row["v"] if row else None

    # ------------------------------------------------------------------
    # keys
    # ------------------------------------------------------------------
    @_locked
    def create_key(
        self,
        key_id: str,
        name: str,
        prefix: str,
        verifier_hex: str,
        permission_mode: str = "all",
    ) -> dict[str, Any]:
        self._db.execute(
            "INSERT INTO keys(key_id, name, prefix, verifier, status, permission_mode, created_at)"
            " VALUES(?,?,?,?, 'active', ?, ?)",
            (key_id, name, prefix, verifier_hex, permission_mode, utcnow()),
        )
        self._db.commit()
        return self.get_key(key_id)  # type: ignore[return-value]

    @_locked
    def get_key(self, key_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM keys WHERE key_id=?", (key_id,)).fetchone()
        return dict(row) if row else None

    @_locked
    def find_active_key_by_verifier(self, verifier_hex: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT * FROM keys WHERE verifier=? AND status='active'", (verifier_hex,)
        ).fetchone()
        return dict(row) if row else None

    @_locked
    def find_key_by_verifier(self, verifier_hex: str) -> dict[str, Any] | None:
        """Return the key record for a verifier regardless of status."""
        row = self._db.execute(
            "SELECT * FROM keys WHERE verifier=?", (verifier_hex,)
        ).fetchone()
        return dict(row) if row else None

    @_locked
    def list_keys(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT key_id, name, prefix, status, permission_mode, created_at,"
            " last_used_at, revoked_at FROM keys ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def revoke_key(self, key_id: str) -> dict[str, Any] | None:
        self._db.execute(
            "UPDATE keys SET status='revoked', revoked_at=? WHERE key_id=? AND status='active'",
            (utcnow(), key_id),
        )
        self._db.commit()
        return self.get_key(key_id)

    @_locked
    def set_key_permission(self, key_id: str, mode: str) -> dict[str, Any] | None:
        self._db.execute(
            "UPDATE keys SET permission_mode=? WHERE key_id=?", (mode, key_id)
        )
        self._db.commit()
        return self.get_key(key_id)

    @_locked
    def touch_key_last_used(self, key_id: str, min_interval_s: int = 60) -> None:
        """Update last_used_at at most once per minute to limit write churn."""
        row = self.get_key(key_id)
        if not row:
            return
        last = row.get("last_used_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if (datetime.now(timezone.utc) - last_dt).total_seconds() < min_interval_s:
                    return
            except ValueError:
                pass
        self._db.execute(
            "UPDATE keys SET last_used_at=? WHERE key_id=?", (utcnow(), key_id)
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # jobs
    # ------------------------------------------------------------------
    @_locked
    def insert_job(
        self,
        job_id: str,
        command: str,
        cwd: str | None,
        pid: int | None,
        key_id: str | None,
        key_name: str | None,
    ) -> None:
        # Command text is persisted (dashboard/CLI display); embedded
        # secrets are masked before storage (PRD section 13).
        command = security.redact_secrets(command)
        self._db.execute(
            "INSERT INTO jobs(job_id, key_id, key_name, command, cwd, status, pid, started_at)"
            " VALUES(?,?,?,?,?, 'running', ?, ?)",
            (job_id, key_id, key_name, command, cwd, pid, utcnow()),
        )
        self._db.commit()

    @_locked
    def update_job(
        self,
        job_id: str,
        status: str,
        exit_code: int | None = None,
        finished: bool = False,
        pid: int | None = None,
    ) -> None:
        self._db.execute(
            "UPDATE jobs SET status=?, exit_code=?, "
            "finished_at=CASE WHEN ? THEN ? ELSE finished_at END,"
            " pid=COALESCE(?, pid) WHERE job_id=?",
            (status, exit_code, 1 if finished else 0, utcnow(), pid, job_id),
        )
        self._db.commit()

    @_locked
    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    @_locked
    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM jobs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def running_jobs(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    @_locked
    def insert_session(
        self,
        session_id: str,
        command: str,
        cwd: str | None,
        pid: int | None,
        key_id: str | None,
        key_name: str | None,
    ) -> None:
        command = security.redact_secrets(command)
        self._db.execute(
            "INSERT INTO sessions(session_id, key_id, key_name, command, cwd, status,"
            " pid, created_at, last_activity) VALUES(?,?,?,?,?, 'open', ?, ?, ?)",
            (session_id, key_id, key_name, command, cwd, pid, utcnow(), utcnow()),
        )
        self._db.commit()

    @_locked
    def update_session(
        self,
        session_id: str,
        status: str | None = None,
        pid: int | None = None,
        touch: bool = True,
        closed: bool = False,
    ) -> None:
        sets = []
        params: list[Any] = []
        if status is not None:
            sets.append("status=?")
            params.append(status)
        if pid is not None:
            sets.append("pid=?")
            params.append(pid)
        if touch:
            sets.append("last_activity=?")
            params.append(utcnow())
        if closed:
            sets.append("closed_at=?")
            params.append(utcnow())
        if not sets:
            return
        params.append(session_id)
        self._db.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE session_id=?", params)
        self._db.commit()

    @_locked
    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    @_locked
    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def open_sessions(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM sessions WHERE status='open'").fetchall()
        return [dict(r) for r in rows]

    @_locked
    def mark_open_sessions(self, status: str) -> None:
        """Used at startup: PTYs of the previous process are gone."""
        self._db.execute(
            "UPDATE sessions SET status=?, closed_at=? WHERE status='open'",
            (status, utcnow()),
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # execution audit log
    # ------------------------------------------------------------------
    @_locked
    def add_log(
        self,
        key_id: str | None,
        key_name: str | None,
        tool: str,
        status: str,
        meta: dict[str, Any] | None = None,
        exit_code: int | None = None,
        duration_ms: int | None = None,
        output_preview: str | None = None,
    ) -> None:
        # Audit-log choke point: command text and output previews are
        # redacted before persistence so credentials embedded by agents in
        # commands (curl headers, git remotes, exports) never land in the
        # audit trail, the dashboard or the journal (PRD section 13).
        meta = dict(meta or {})
        if isinstance(meta.get("command"), str):
            meta["command"] = security.redact_secrets(meta["command"])
        if output_preview:
            output_preview = security.redact_secrets(output_preview)
        self._db.execute(
            "INSERT INTO exec_logs(ts, key_id, key_name, tool, meta, status, exit_code,"
            " duration_ms, output_preview) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                utcnow(),
                key_id,
                key_name,
                tool,
                json.dumps(meta, ensure_ascii=False),
                status,
                exit_code,
                duration_ms,
                (output_preview or None) and output_preview[:4096],
            ),
        )
        self._db.commit()

    @_locked
    def list_logs(self, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM exec_logs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["meta"] = json.loads(d.get("meta") or "{}")
            except json.JSONDecodeError:
                d["meta"] = {}
            out.append(d)
        return out

    @_locked
    def count_logs(self) -> int:
        row = self._db.execute("SELECT COUNT(*) AS c FROM exec_logs").fetchone()
        return int(row["c"])

    @_locked
    def trim_exec_logs(self, keep: int = 20_000) -> int:
        """Retention pass over the audit log (B-11): delete the OLDEST rows
        beyond ``keep``. Called once at server startup so a long-lived VPS
        does not accumulate unbounded exec_logs growth (each row carries a
        redacted command + up-to-4 KiB output preview). Returns the number
        of rows removed.
        """
        if keep < 1:
            return 0
        cur = self._db.execute(
            "DELETE FROM exec_logs WHERE id < "
            "(SELECT COALESCE(MIN(id), 0) FROM "
            "  (SELECT id FROM exec_logs ORDER BY id DESC LIMIT ?))",
            (keep,),
        )
        removed = cur.rowcount or 0
        # ALWAYS commit: in Python's sqlite3 a DML statement opens a write
        # transaction that stays open until commit(); skipping the commit on
        # a zero-row delete left the server holding the database write lock
        # indefinitely ("database is locked" for every other writer).
        self._db.commit()
        return removed

    @_locked
    def quick_check(self) -> str:
        """SQLite integrity check ('ok' when the database is healthy)."""
        try:
            row = self._db.execute("PRAGMA quick_check").fetchone()
            return str(row[0]) if row else "unknown"
        except sqlite3.DatabaseError as exc:
            return f"failed: {exc}"

    # ------------------------------------------------------------------
    @_locked
    def close(self) -> None:
        try:
            self._db.close()
        except sqlite3.DatabaseError:
            pass

    # ------------------------------------------------------------------
    # summary helpers
    # ------------------------------------------------------------------
    @_locked
    def summary(self) -> dict[str, Any]:
        def _count(sql: str, params: tuple = ()) -> int:
            row = self._db.execute(sql, params).fetchone()
            return int(list(row)[0])

        return {
            "keys_active": _count("SELECT COUNT(*) FROM keys WHERE status='active'"),
            "keys_revoked": _count("SELECT COUNT(*) FROM keys WHERE status='revoked'"),
            "jobs_running": _count("SELECT COUNT(*) FROM jobs WHERE status='running'"),
            "sessions_open": _count("SELECT COUNT(*) FROM sessions WHERE status='open'"),
            "log_entries": _count("SELECT COUNT(*) FROM exec_logs"),
        }
