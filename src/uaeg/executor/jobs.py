"""Background job management (PRD FR-009).

A background job is a detached process tree that outlives HTTP requests,
client disconnects and UAEG restarts. State on disk per job:

    <data_dir>/jobs/<job_id>/stdout   captured stdout (append)
    <data_dir>/jobs/<job_id>/stderr   captured stderr (append)
    <data_dir>/jobs/<job_id>/pid      pid of the actual command (written by shim)
    <data_dir>/jobs/<job_id>/exit     exit code, written by the shim on finish

In-memory watchers poll each running job's shim so the database reflects
status/exit code promptly. On startup, ``reattach()`` reconciles persisted
metadata with reality: dead pid + exit file  -> final state; dead pid + no
exit file (e.g. reboot or SIGKILL) -> 'interrupted'; live pid -> still
running and a watcher is re-attached.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import paths
from ..db import Store

log = logging.getLogger("uaeg.jobs")

FINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}


class JobError(Exception):
    pass


@dataclass
class JobRuntime:
    job_id: str
    shim_pid: int
    task: asyncio.Task | None = None


class JobManager:
    def __init__(self, store: Store, shell: str = "/bin/bash", poll_interval: float = 0.5):
        self.store = store
        self.shell = shell
        self.poll_interval = poll_interval
        self._runtime: dict[str, JobRuntime] = {}
        self._started_at = time.monotonic()

    # ------------------------------------------------------------------
    # spawn
    # ------------------------------------------------------------------
    def spawn(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        key_id: str | None = None,
        key_name: str | None = None,
    ) -> dict[str, Any]:
        job_id = "job-" + uuid.uuid4().hex
        job_dir = paths.jobs_dir() / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(job_dir, 0o700)
        except OSError:
            pass

        import subprocess

        full_env = os.environ.copy()
        if env:
            for k, v in env.items():
                full_env[k] = str(v)

        out_f = open(job_dir / "stdout", "ab", buffering=0)
        err_f = open(job_dir / "stderr", "ab", buffering=0)
        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "uaeg._jobwrap",
                    str(job_dir),
                    "--",
                    self.shell,
                    "-c",
                    command,
                ],
                stdin=subprocess.DEVNULL,
                stdout=out_f,
                stderr=err_f,
                cwd=cwd or None,
                env=full_env,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            out_f.close()
            err_f.close()
            raise JobError(f"failed to spawn background job: {exc}") from exc
        finally:
            # The child inherited its own descriptors; drop ours.
            out_f.close()
            err_f.close()

        self.store.insert_job(
            job_id=job_id,
            command=command,
            cwd=cwd,
            pid=proc.pid,
            key_id=key_id,
            key_name=key_name,
        )
        runtime = JobRuntime(job_id=job_id, shim_pid=proc.pid)
        self._runtime[job_id] = runtime
        runtime.task = asyncio.get_running_loop().create_task(
            self._watch(runtime), name=f"uaeg-job-{job_id}"
        )
        log.info("job %s started (shim pid %d): %s", job_id, proc.pid, command[:200])
        return self.status(job_id) or {"job_id": job_id, "status": "running"}

    # ------------------------------------------------------------------
    # status / output
    # ------------------------------------------------------------------
    def status(self, job_id: str) -> dict[str, Any] | None:
        job = self.store.get_job(job_id)
        return job

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_jobs(limit=limit)

    def output(
        self,
        job_id: str,
        stream: str = "stdout",
        offset: int = 0,
        limit: int = 65536,
    ) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if not job:
            raise JobError(f"unknown job_id: {job_id}")
        if stream not in ("stdout", "stderr"):
            raise JobError("stream must be 'stdout' or 'stderr'")
        path = paths.jobs_dir() / job_id / stream
        if not path.exists():
            return {
                "job_id": job_id,
                "stream": stream,
                "data": "",
                "offset": 0,
                "available": 0,
                "status": job["status"],
                "exit_code": job.get("exit_code"),
            }
        size = path.stat().st_size
        offset = max(0, min(offset, size))
        limit = max(0, min(limit, 1_048_576))
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read(limit)
        return {
            "job_id": job_id,
            "stream": stream,
            "data": data.decode("utf-8", errors="replace"),
            "offset": offset + len(data),
            "available": size,
            "status": job["status"],
            "exit_code": job.get("exit_code"),
        }

    async def wait(self, job_id: str, timeout_s: float | None = 30.0) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if not job:
            raise JobError(f"unknown job_id: {job_id}")
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            job = self.store.get_job(job_id)
            if not job or job["status"] in FINAL_STATES:
                return job or {"job_id": job_id, "status": "unknown"}
            if deadline is not None and time.monotonic() >= deadline:
                return job
            await asyncio.sleep(0.25)

    # ------------------------------------------------------------------
    # cancel
    # ------------------------------------------------------------------
    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if not job:
            raise JobError(f"unknown job_id: {job_id}")
        if job["status"] in FINAL_STATES:
            return job

        # Mark cancelled BEFORE killing so the watcher cannot race us into
        # 'interrupted' (the kill produces no exit file by design).
        self.store.update_job(job_id, "cancelled", exit_code=None, finished=True)

        pid = job.get("pid")
        if pid:
            # Kill the whole process group (shim + command tree).
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        os.kill(pid, sig)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                await asyncio.sleep(0.4)
                job = self.store.get_job(job_id)
                if job and job["status"] in FINAL_STATES:
                    break

        # If the process actually finished concurrently, prefer its exit code.
        exit_code = self._read_exit_file(job_id)
        if exit_code is not None:
            status = "completed" if exit_code == 0 else "failed"
            self.store.update_job(job_id, status, exit_code=exit_code, finished=True)

        runtime = self._runtime.get(job_id)
        if runtime and runtime.task:
            runtime.task.cancel()
            self._runtime.pop(job_id, None)
        log.info("job %s cancelled", job_id)
        return self.store.get_job(job_id) or job

    # ------------------------------------------------------------------
    # watcher & startup reconciliation
    # ------------------------------------------------------------------
    async def _watch(self, runtime: JobRuntime) -> None:
        try:
            while True:
                await asyncio.sleep(self.poll_interval)
                if self._check_once(runtime.job_id, runtime.shim_pid):
                    return
        except asyncio.CancelledError:
            pass

    def _check_once(self, job_id: str, shim_pid: int | None) -> bool:
        """Return True when the job reached a final state."""
        job = self.store.get_job(job_id)
        if not job or job["status"] in FINAL_STATES:
            self._runtime.pop(job_id, None)
            return True
        exit_code = self._read_exit_file(job_id)
        if exit_code is not None:
            status = "completed" if exit_code == 0 else "failed"
            self.store.update_job(job_id, status, exit_code=exit_code, finished=True)
            self._runtime.pop(job_id, None)
            log.info("job %s finished: %s (exit %s)", job_id, status, exit_code)
            return True
        pid_alive = shim_pid is not None and _pid_alive(shim_pid)
        if not pid_alive:
            # Shim died without writing an exit file (reboot or SIGKILL).
            self.store.update_job(job_id, "interrupted", exit_code=None, finished=True)
            self._runtime.pop(job_id, None)
            log.warning("job %s interrupted (process gone, no exit recorded)", job_id)
            return True
        return False

    def _read_exit_file(self, job_id: str) -> int | None:
        path = paths.jobs_dir() / job_id / "exit"
        try:
            return int(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None

    def reattach(self) -> dict[str, int]:
        """Reconcile persisted job metadata with actual process state."""
        counts = {"running": 0, "finalized": 0, "interrupted": 0}
        for job in self.store.running_jobs():
            job_id = job["job_id"]
            pid = job.get("pid")
            if self._read_exit_file(job_id) is not None:
                self._check_once(job_id, pid)  # finalize from exit file
                counts["finalized"] += 1
                continue
            if pid and _pid_alive(pid):
                runtime = JobRuntime(job_id=job_id, shim_pid=pid)
                self._runtime[job_id] = runtime
                runtime.task = asyncio.get_running_loop().create_task(
                    self._watch(runtime), name=f"uaeg-job-{job_id}"
                )
                counts["running"] += 1
                log.info("job %s re-attached (pid %s still running)", job_id, pid)
            else:
                self.store.update_job(job_id, "interrupted", finished=True)
                counts["interrupted"] += 1
                log.warning(
                    "job %s marked interrupted (pid %s not alive, no exit file; "
                    "likely a reboot or external kill)",
                    job_id,
                    pid,
                )
        return counts

    async def shutdown(self) -> None:
        """Stop watchers WITHOUT killing the detached jobs themselves."""
        for runtime in list(self._runtime.values()):
            if runtime.task:
                runtime.task.cancel()
        self._runtime.clear()


def _pid_alive(pid: int | None) -> bool:
    """True only for live (non-zombie) processes.

    A SIGKILLed shim whose parent is still alive becomes a zombie; kill(pid,0)
    would wrongly report it as running. /proc/<pid>/stat state 'Z' = dead.
    """
    if not pid:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            stat = fh.read()
        # pid (comm) state ... - comm may contain spaces/parens; split after last ')'
        tail = stat.rsplit(")", 1)
        if len(tail) == 2:
            state = tail[1].split()[0] if tail[1].split() else "?"
            return state not in ("Z", "X", "x")
        return True
    except (OSError, IndexError):
        # /proc unavailable: fall back to the classic signal probe
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists, owned by someone else
        except OSError:
            return False
