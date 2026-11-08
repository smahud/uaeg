"""Process containment helpers (revocation completeness, PRD FR-012).

Revoking a key must not leave that key's running work behind: an agent whose
key was revoked because of compromise must not keep interactive shells or
background jobs running on the host. These helpers terminate:

- live PTY sessions belonging to the key, and
- running background jobs belonging to the key,

either from INSIDE the server process (dashboard: graceful, closes PTY
master fds via SessionManager/JobManager) or from OUTSIDE (CLI: direct
process-group signals against the pids persisted in the database).

Rotation is deliberately NOT containment: rotation is routine credential
hygiene where the same agent continues with a new secret.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from typing import Any

from .db import Store

log = logging.getLogger("uaeg.containment")


def _kill_group(pid: int, grace_s: float = 1.5) -> None:
    """SIGTERM the process group, then SIGKILL if it survives the grace."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                return
        if sig == signal.SIGTERM and grace_s > 0:
            time.sleep(grace_s)


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            stat = fh.read()
        tail = stat.rsplit(")", 1)
        if len(tail) == 2:
            state = tail[1].split()[0] if tail[1].split() else "?"
            return state not in ("Z", "X", "x")
        return True
    except (OSError, IndexError):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False


def key_resources(store: Store, key_id: str) -> dict[str, list[dict[str, Any]]]:
    """Live resources belonging to a key: open sessions + running jobs."""
    # B-2: query OPEN sessions directly. The previous list_sessions(limit=1000)
    # only saw the newest 1000 session rows - once a busy deployment crossed
    # that boundary, a revoked key's older-but-still-open PTY session silently
    # survived CLI revocation (FR-012 regression).
    sessions = [
        s for s in store.open_sessions() if s.get("key_id") == key_id
    ]
    jobs = [j for j in store.running_jobs() if j.get("key_id") == key_id]
    return {"sessions": sessions, "jobs": jobs}


def terminate_key_processes(store: Store, key_id: str) -> dict[str, Any]:
    """Out-of-server containment (CLI path): signal the process groups of
    every live session/job owned by the key and persist final statuses.

    Safe to run while the UAEG server is up: the server's own reapers notice
    the dying processes and update state consistently; the watcher for a job
    marked 'cancelled' here stops immediately (final state).
    """
    res = key_resources(store, key_id)
    terminated_sessions = 0
    terminated_jobs = 0

    for session in res["sessions"]:
        pid = session.get("pid")
        if pid:
            try:
                # SIGHUP first: the PTY session leader dies like on a
                # disconnect; escalate to SIGKILL if it lingers.
                try:
                    os.killpg(pid, signal.SIGHUP)
                except (ProcessLookupError, PermissionError, OSError):
                    os.kill(pid, signal.SIGHUP)
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline and _pid_alive(pid):
                    time.sleep(0.05)
                if _pid_alive(pid):
                    _kill_group(pid, grace_s=0.5)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        store.update_session(session["session_id"], status="closed", closed=True)
        terminated_sessions += 1

    for job in res["jobs"]:
        job_id = job["job_id"]
        # Mark cancelled BEFORE killing so the server watcher cannot race
        # the kill into 'interrupted'.
        store.update_job(job_id, "cancelled", exit_code=None, finished=True)
        pid = job.get("pid")
        if pid:
            _kill_group(pid, grace_s=0.6)
        terminated_jobs += 1

    if terminated_sessions or terminated_jobs:
        log.warning(
            "containment for key %s: terminated %d session(s), %d job(s)",
            key_id, terminated_sessions, terminated_jobs,
        )
    return {
        "terminated_sessions": terminated_sessions,
        "terminated_jobs": terminated_jobs,
    }


def cancel_job_standalone(store: Store, job_id: str) -> dict[str, Any] | None:
    """Cancel a background job from outside the server process (CLI path).

    Returns the final job row, or None when the job_id is unknown.
    """
    job = store.get_job(job_id)
    if not job:
        return None
    if job["status"] in ("completed", "failed", "cancelled", "interrupted"):
        return job
    store.update_job(job_id, "cancelled", exit_code=None, finished=True)
    pid = job.get("pid")
    if pid:
        _kill_group(pid, grace_s=0.8)
        # If the shim managed to write its exit file before dying, prefer
        # the real outcome (mirrors the in-server JobManager.cancel).
        exit_path = None
        try:
            from . import paths

            exit_path = paths.jobs_dir() / job_id / "exit"
            code = int(exit_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            code = None
        if code is not None:
            status = "completed" if code == 0 else "failed"
            store.update_job(job_id, status, exit_code=code, finished=True)
    return store.get_job(job_id) or job


def kill_session_standalone(store: Store, session_id: str) -> dict[str, Any] | None:
    """Terminate a PTY session from outside the server process (CLI path).

    Sends SIGHUP (then SIGKILL) to the session leader's process group and
    records the closed status. The server (if running) observes the exit and
    releases its master fd. Returns the session row, or None if unknown.
    """
    session = store.get_session(session_id)
    if not session:
        return None
    if session["status"] != "open":
        return session
    pid = session.get("pid")
    if pid:
        try:
            try:
                os.killpg(pid, signal.SIGHUP)
            except (ProcessLookupError, PermissionError, OSError):
                os.kill(pid, signal.SIGHUP)
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and _pid_alive(pid):
                time.sleep(0.05)
            if _pid_alive(pid):
                _kill_group(pid, grace_s=0.5)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    store.update_session(session_id, status="closed", closed=True)
    return store.get_session(session_id) or session
