"""Synchronous shell execution (PRD FR-007).

``run_command`` executes a shell command on the VPS host with:

- configurable working directory and environment overrides,
- an execution timeout (process group is killed on expiry),
- bounded stdout/stderr capture (per-stream cap; the remainder is counted,
  never accumulated in memory, and reported as truncated).

Background/asynchronous execution is handled by executor.jobs.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass, field


@dataclass
class ExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None
    extra: dict = field(default_factory=dict)


class _CappedBuffer:
    """Accumulates at most ``cap`` bytes; counts the true total."""

    __slots__ = ("cap", "chunks", "held", "total")

    def __init__(self, cap: int):
        self.cap = cap
        self.chunks: list[bytes] = []
        self.held = 0
        self.total = 0

    def feed(self, data: bytes) -> None:
        self.total += len(data)
        if self.held < self.cap:
            room = self.cap - self.held
            self.chunks.append(data[:room])
            self.held += min(len(data), room)

    def text(self) -> str:
        return b"".join(self.chunks).decode("utf-8", errors="replace")

    @property
    def truncated(self) -> bool:
        return self.total > self.held


async def _pump(stream: asyncio.StreamReader, buf: _CappedBuffer) -> None:
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        buf.feed(chunk)


async def run_command(
    command: str,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float = 120,
    max_output_bytes: int = 1_048_576,
    shell: str = "/bin/bash",
) -> ExecResult:
    """Run ``command`` and wait for completion (with timeout)."""
    started = time.monotonic()

    full_env = os.environ.copy()
    if env:
        for k, v in env.items():
            full_env[k] = str(v)

    try:
        proc = await asyncio.create_subprocess_exec(
            shell,
            "-c",
            command,
            cwd=cwd or None,
            env=full_env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except (NotADirectoryError, FileNotFoundError, PermissionError, OSError) as exc:
        return ExecResult(
            exit_code=None,
            stdout="",
            stderr=str(exc),
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"failed to start command: {exc}",
        )

    out_buf = _CappedBuffer(max_output_bytes)
    err_buf = _CappedBuffer(max_output_bytes)
    pumps = [
        asyncio.create_task(_pump(proc.stdout, out_buf)),
        asyncio.create_task(_pump(proc.stderr, err_buf)),
    ]

    async def _drain(timeout: float = 5.0) -> None:
        """Wait for the pumps to hit EOF so no buffered output is lost.

        The process exiting does NOT mean its output was read yet - data can
        still sit in the pipe buffer. Only if inherited writers (e.g. daemonized
        children) keep the pipe open do we stop waiting after `timeout` seconds.
        """
        try:
            await asyncio.wait_for(asyncio.gather(*pumps), timeout=timeout)
        except asyncio.TimeoutError:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)

    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        timed_out = True
        _kill_process_group(proc.pid)
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    finally:
        await _drain()

    duration_ms = int((time.monotonic() - started) * 1000)
    exit_code = None if timed_out else proc.returncode

    result = ExecResult(
        exit_code=exit_code,
        stdout=out_buf.text(),
        stderr=err_buf.text(),
        duration_ms=duration_ms,
        timed_out=timed_out,
        stdout_bytes=out_buf.total,
        stderr_bytes=err_buf.total,
        stdout_truncated=out_buf.truncated,
        stderr_truncated=err_buf.truncated,
    )
    if timed_out:
        result.error = (
            f"command timed out after {timeout_s:.0f}s; process group was killed; "
            "use background execution for long-running commands"
        )
    return result


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def result_to_dict(res: ExecResult) -> dict:
    return {
        "exit_code": res.exit_code,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "duration_ms": res.duration_ms,
        "timed_out": res.timed_out,
        "stdout_bytes": res.stdout_bytes,
        "stderr_bytes": res.stderr_bytes,
        "stdout_truncated": res.stdout_truncated,
        "stderr_truncated": res.stderr_truncated,
        **({"error": res.error} if res.error else {}),
        **res.extra,
    }
