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
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import paths, security as _security
from ..db import Store

log = logging.getLogger("uaeg.jobs")

FINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}

_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"


def _boot_id() -> str:
    """Current kernel boot id (empty when /proc is unavailable)."""
    try:
        with open(_BOOT_ID_PATH, encoding="ascii") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _group_alive(pgid: int) -> bool:
    """True while the process group has at least one LIVE (non-zombie) member.

    Used for cancellation escalation (B-3): the DB status was already set to
    'cancelled' before the signals are sent, so only actual process-group
    liveness can decide whether SIGKILL is still needed. Zombies keep their
    pgid after death, so a killpg(pid, 0) probe would over-count them and
    trigger pointless extra SIGKILL rounds; scanning /proc distinguishes
    live members from reaped-pending zombies.
    """
    try:
        entries = list(os.scandir("/proc"))
    except OSError:
        # /proc unavailable: fall back to the signal probe (may over-count
        # zombies - a harmless extra SIGKILL to a dead group at worst).
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            with open(f"/proc/{entry.name}/stat", encoding="ascii") as fh:
                stat = fh.read()
        except OSError:
            continue
        tail = stat.rsplit(")", 1)
        if len(tail) != 2:
            continue
        fields = tail[1].split()
        if not fields:
            continue
        state = fields[0]
        if state in ("Z", "X", "x"):
            continue  # dead, awaiting reaping - not a live member
        try:
            if int(fields[2]) == pgid:  # fields: state, ppid, pgrp, ...
                return True
        except (IndexError, ValueError):
            continue
    return False


class JobError(Exception):
    pass


@dataclass
class JobRuntime:
    job_id: str
    shim_pid: int
    task: asyncio.Task | None = None
    # The Popen handle of the shim (None for jobs re-attached after a
    # restart - those shims are children of the PREVIOUS server process
    # and are reaped by init, not by us). Keeping the handle lets the
    # watcher reap the shim deterministically instead of leaving a zombie
    # until the next unrelated subprocess call (v0.3.4).
    proc: subprocess.Popen | None = None


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

        full_env = os.environ.copy()
        if env:
            for k, v in env.items():
                full_env[k] = str(v)

        out_f = open(job_dir / "stdout", "ab", buffering=0)
        err_f = open(job_dir / "stderr", "ab", buffering=0)
        # Stamp the kernel boot id (B-4): after a reboot, pids are recycled and
        # pid-liveness alone could keep a dead job 'running' (or even aim
        # containment signals at an unrelated process). reattach() compares
        # the stamp against the CURRENT boot id and marks such jobs
        # 'interrupted' unconditionally.
        try:
            (job_dir / "boot_id").write_text(_boot_id() + "\n", encoding="ascii")
        except OSError:
            pass
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

        try:
            self.store.insert_job(
                job_id=job_id,
                command=command,
                cwd=cwd,
                pid=proc.pid,
                key_id=key_id,
                key_name=key_name,
            )
        except Exception as exc:
            # v0.3.4: NEVER leave an unmanaged live process behind. If the
            # job row cannot be recorded (disk full, db locked), kill the
            # shim we just spawned and surface a clean JobError (the MCP
            # tool layer maps JobError to an agent-visible error response;
            # previously a raw sqlite3 exception escaped and the process ran
            # on with no job_id, no watcher and no cancel path).
            log.error(
                "job %s could not be recorded; killing shim pid %d: %s",
                job_id, proc.pid, exc,
            )
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            raise JobError(f"job could not be recorded (database error): {exc}") from exc
        runtime = JobRuntime(job_id=job_id, shim_pid=proc.pid, proc=proc)
        self._runtime[job_id] = runtime
        runtime.task = asyncio.get_running_loop().create_task(
            self._watch(runtime), name=f"uaeg-job-{job_id}"
        )
        log.info(
            "job %s started (shim pid %d): %s",
            job_id, proc.pid, _security.redact_secrets(command)[:200],
        )
        return self.status(job_id) or {"job_id": job_id, "status": "running"}

    # ------------------------------------------------------------------
    # status / output
    # ------------------------------------------------------------------
    def _gate(
        self, job_id: str, owner_key_id: str | None = None
    ) -> dict[str, Any]:
        """Fetch a job row, enforcing key-scoped ownership when requested.

        ``owner_key_id`` is given by the MCP tool layer (the calling agent
        key): a job started by key A is invisible to key B. The dashboard,
        the CLI and revocation containment pass None (operator level) and
        see everything. Rows with a NULL key_id are operator-level
        artifacts and stay visible to everyone.
        """
        job = self.store.get_job(job_id)
        if not job:
            raise JobError(f"unknown job_id: {job_id}")
        if (
            owner_key_id is not None
            and job.get("key_id") is not None
            and job.get("key_id") != owner_key_id
        ):
            raise JobError(f"job {job_id} belongs to a different agent key")
        return job

    def status(
        self, job_id: str, owner_key_id: str | None = None
    ) -> dict[str, Any] | None:
        return self._gate(job_id, owner_key_id)

    def list(
        self, limit: int = 50, owner_key_id: str | None = None
    ) -> list[dict[str, Any]]:
        jobs = self.store.list_jobs(limit=limit)
        if owner_key_id is not None:
            jobs = [
                j for j in jobs if j.get("key_id") is None or j.get("key_id") == owner_key_id
            ]
        return jobs

    def output(
        self,
        job_id: str,
        stream: str = "stdout",
        offset: int = 0,
        limit: int = 65536,
        owner_key_id: str | None = None,
    ) -> dict[str, Any]:
        job = self._gate(job_id, owner_key_id)
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

    async def wait(
        self,
        job_id: str,
        timeout_s: float | None = 30.0,
        owner_key_id: str | None = None,
    ) -> dict[str, Any]:
        job = self._gate(job_id, owner_key_id)
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
    async def cancel(
        self, job_id: str, owner_key_id: str | None = None
    ) -> dict[str, Any]:
        job = self._gate(job_id, owner_key_id)
        if job["status"] in FINAL_STATES:
            return job

        # Mark cancelled BEFORE killing so the watcher cannot race us into
        # 'interrupted' (the kill produces no exit file by design).
        self.store.update_job(job_id, "cancelled", exit_code=None, finished=True)

        pid = job.get("pid")
        if pid:
            # Kill the whole process group (shim + command tree). Escalation
            # to SIGKILL is decided by ACTUAL process-group liveness (B-3):
            # the status row was already set to 'cancelled' above, so checking
            # the DB here (as v0.3.2 did) always broke early and left
            # TERM-ignoring commands (nohup, `trap '' TERM`, daemons) running.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        os.kill(pid, sig)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                await asyncio.sleep(0.4)
                if not _group_alive(pid):
                    break

        # If the process actually finished concurrently, prefer its exit code.
        exit_code = self._read_exit_file(job_id)
        if exit_code is not None:
            status = "completed" if exit_code == 0 else "failed"
            self.store.update_job(job_id, status, exit_code=exit_code, finished=True)

        runtime = self._runtime.pop(job_id, None)
        if runtime and runtime.task:
            runtime.task.cancel()
        if runtime is not None:
            # Deterministic shim reap (v0.3.4): a killed shim must not linger
            # as a zombie until the next unrelated subprocess call.
            await self._reap_shim(runtime)
        log.info("job %s cancelled", job_id)
        return self.store.get_job(job_id) or job

    async def cancel_by_key(self, key_id: str) -> int:
        """Revocation containment: cancel every running job owned by a key.
        Returns the number of jobs cancelled."""
        count = 0
        for job in self.store.running_jobs():
            if job.get("key_id") != key_id:
                continue
            try:
                await self.cancel(job["job_id"])
                count += 1
            except JobError:
                pass
        return count

    # ------------------------------------------------------------------
    # watcher & startup reconciliation
    # ------------------------------------------------------------------
    async def _watch(self, runtime: JobRuntime) -> None:
        try:
            while True:
                await asyncio.sleep(self.poll_interval)
                try:
                    if self._check_once(runtime.job_id, runtime.shim_pid):
                        await self._reap_shim(runtime)
                        return
                except Exception:  # noqa: BLE001 - a transient DB error must
                    # not kill the watcher and leave the job 'running' forever
                    # with no reconciliation (B-5).
                    log.exception(
                        "job %s watcher check failed; will retry next poll",
                        runtime.job_id,
                    )
        except asyncio.CancelledError:
            pass

    async def _reap_shim(self, runtime: JobRuntime) -> None:
        """Reap the shim process of a finished/killed job (v0.3.4).

        The dropped Popen reference used to leave the dead shim in zombie
        state until some LATER subprocess call happened to reap it. Bounded
        wait: the shim has either written its exit file (it is exiting) or
        was just signalled by cancel().
        """
        proc = runtime.proc
        if proc is None:
            return
        try:
            await asyncio.to_thread(proc.wait, 10)
        except Exception:  # noqa: BLE001 - reaping is best-effort
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
        current_boot = _boot_id()
        for job in self.store.running_jobs():
            job_id = job["job_id"]
            pid = job.get("pid")
            job_dir = paths.jobs_dir() / job_id
            # Boot-id guard (B-4): a job spawned under a PREVIOUS boot cannot
            # still have a live pid in this boot - a pid that exists now is a
            # recycled, unrelated process. Mark it interrupted instead of
            # trusting pid liveness (and never signal that pid later).
            stamped_boot = ""
            try:
                stamped_boot = (job_dir / "boot_id").read_text(encoding="ascii").strip()
            except OSError:
                pass  # legacy job rows (pre-0.3.3): fall back to pid liveness
            if stamped_boot and current_boot and stamped_boot != current_boot:
                self.store.update_job(job_id, "interrupted", finished=True)
                counts["interrupted"] += 1
                log.warning(
                    "job %s marked interrupted (created in a previous boot; "
                    "pid %s may have been recycled)",
                    job_id,
                    pid,
                )
                continue
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
