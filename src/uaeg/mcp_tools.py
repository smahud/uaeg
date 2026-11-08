"""MCP tool definitions (PRD FR-007, FR-008, FR-009, FR-010, FR-013, FR-014).

Tool surface exposed to hosted agents over MCP:

    shell_exec        run a command (sync with timeout, or background job)
    shell_open        open a persistent interactive PTY session
    shell_input       send input (incl. control chars) to a session
    shell_output      read new session output since a sequence cursor
    shell_resize      resize a session terminal
    shell_close       terminate a session
    shell_sessions    list live sessions (reconnection support)
    job_status        status of a background job
    job_output        paginated background job output
    job_wait          wait (bounded) for a background job to finish
    job_cancel        cancel a background job
    job_list          list recent background jobs
    server_info       identity, usage rules and operational defaults

Every tool call is recorded in the execution audit log with the calling
key's identity (PRD FR-016 Logs, section 13).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from . import __version__
from .auth import request_key
from .executor.jobs import JobError
from .executor.pty import SessionError
from .executor.runner import result_to_dict, run_command
from .runtime import Runtime

log = logging.getLogger("uaeg.tools")

MCP_INSTRUCTIONS = (
    "UAEG (Universal Agent Execution Gateway) executes commands natively on "
    "this VPS on your behalf. Commands are real, persistent and run with the "
    "privileges of the UAEG service user (root on a standard install). "
    "Prefer shell_exec for automation; use PTY sessions only for interactive "
    "programs; use background jobs for anything long-running. Call server_info "
    "first to learn the execution context and any advisory usage rules."
)


def _key_summary(key: dict[str, Any] | None) -> dict[str, Any] | None:
    if not key:
        return None
    return {
        "key_id": key.get("key_id"),
        "name": key.get("name"),
        "permission_mode": key.get("permission_mode"),
    }


def register_tools(mcp: FastMCP, runtime: Runtime) -> None:
    store = runtime.store

    def _key(ctx) -> dict[str, Any] | None:
        return request_key(ctx, store)

    def _key_display(key: dict[str, Any] | None) -> tuple[str | None, str | None]:
        if not key:
            return None, None
        return key.get("key_id"), key.get("name")

    def _audit(
        ctx, tool: str, status: str, meta: dict | None = None,
        exit_code: int | None = None, duration_ms: int | None = None,
        output_preview: str | None = None, key: dict | None = None,
    ) -> None:
        key = key or _key(ctx)
        key_id, key_name = _key_display(key)
        try:
            store.add_log(
                key_id=key_id, key_name=key_name, tool=tool, status=status,
                meta=meta, exit_code=exit_code, duration_ms=duration_ms,
                output_preview=output_preview,
            )
        except Exception:  # noqa: BLE001 - auditing must never break execution
            log.exception("audit log write failed")

    def _default_cwd() -> str:
        return os.path.expanduser(runtime.config.exec.default_cwd)

    def _preview(stdout: str, stderr: str, limit: int = 2048) -> str:
        text = (stdout + ("\n--stderr--\n" + stderr if stderr else "")).strip()
        return text[:limit]

    # ==================================================================
    # shell_exec
    # ==================================================================
    @mcp.tool()
    async def shell_exec(
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        background: bool = False,
        ctx: Context | None = None,
    ) -> dict:
        """Execute a shell command on the VPS host.

        Runs via /bin/bash -c with the UAEG service privileges (root on a
        standard install). Synchronous by default: waits up to
        timeout_seconds (default 120) then kills the process group and
        reports timed_out=true. Set background=true for long-running
        commands: returns a job_id immediately; poll job_status/job_output.

        Args:
            command: Shell command line to execute.
            cwd: Working directory (default: the service user's home).
            env: Optional environment variable overrides (merged over the
                server environment).
            timeout_seconds: Sync-mode timeout in seconds (default 120).
            background: Run as a detached background job instead.

        Returns:
            Sync: exit_code, stdout, stderr (bounded), duration_ms, timed_out,
            truncated flags and total byte counts.
            Background: job_id, pid, status='running'.
        """
        key = _key(ctx)
        started = time.monotonic()
        cwd = cwd or _default_cwd()
        meta = {"command": command, "cwd": cwd, "background": background}

        denied = runtime.check_restricted_cwd(cwd, key)
        if denied:
            _audit(ctx, "shell_exec", "denied", meta, key=key)
            return {"ok": False, "error": denied}

        if background:
            try:
                job = runtime.jobs.spawn(
                    command=command, cwd=cwd, env=env,
                    key_id=key.get("key_id") if key else None,
                    key_name=key.get("name") if key else None,
                )
            except JobError as exc:
                _audit(ctx, "shell_exec", "error", meta, key=key)
                return {"ok": False, "error": str(exc)}
            _audit(ctx, "shell_exec", "ok", {**meta, "job_id": job["job_id"]}, key=key)
            return {
                "ok": True, "background": True, "job_id": job["job_id"],
                "pid": job.get("pid"), "status": job["status"],
                "note": "poll job_status(job_id) and retrieve output with job_output()",
            }

        timeout_s = float(
            timeout_seconds
            if timeout_seconds is not None
            else runtime.config.exec.default_timeout_s
        )
        result = await run_command(
            command,
            cwd=cwd,
            env=env,
            timeout_s=timeout_s,
            max_output_bytes=runtime.config.exec.max_output_bytes,
            shell=runtime.config.exec.shell,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        status = "timeout" if result.timed_out else ("ok" if result.exit_code == 0 else "error")
        _audit(
            ctx, "shell_exec", status, meta,
            exit_code=result.exit_code, duration_ms=duration_ms,
            output_preview=_preview(result.stdout, result.stderr), key=key,
        )
        out = result_to_dict(result)
        out["ok"] = result.error is None and result.exit_code == 0
        return out

    # ==================================================================
    # PTY sessions
    # ==================================================================
    @mcp.tool()
    async def shell_open(
        command: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = 120,
        rows: int = 30,
        ctx: Context | None = None,
    ) -> dict:
        """Open a persistent interactive terminal session (native PTY).

        Leave `command` empty for an interactive login shell, or set it to
        launch a specific program (e.g. `python3`, `htop`, `mysql -u root`).
        The session survives MCP request lifecycles; reconnect any time and
        read new output with shell_output(session_id, since_seq=N).

        Args:
            command: Optional program to run instead of the default shell.
            cwd: Working directory.
            env: Optional environment variable overrides.
            cols: Initial terminal width in columns.
            rows: Initial terminal height in rows.
        """
        key = _key(ctx)
        cwd = cwd or _default_cwd()
        meta = {"command": command or "(interactive shell)", "cwd": cwd}
        denied = runtime.check_restricted_cwd(cwd, key)
        if denied:
            _audit(ctx, "shell_open", "denied", meta, key=key)
            return {"ok": False, "error": denied}
        try:
            info = runtime.sessions.open(
                command=command, cwd=cwd, env=env, cols=cols, rows=rows,
                key_id=key.get("key_id") if key else None,
                key_name=key.get("name") if key else None,
            )
        except SessionError as exc:
            _audit(ctx, "shell_open", "error", meta, key=key)
            return {"ok": False, "error": str(exc)}
        _audit(ctx, "shell_open", "ok", {**meta, "session_id": info["session_id"]}, key=key)
        return {"ok": True, **info}

    @mcp.tool()
    async def shell_input(
        session_id: str,
        data: str,
        add_newline: bool = False,
        ctx: Context | None = None,
    ) -> dict:
        """Send input to an interactive terminal session.

        Control characters work: send "\\x03" for Ctrl-C, "\\x04" for EOF,
        "\\r" for Enter. Set add_newline=true to append a newline for you.

        Args:
            session_id: Session identifier from shell_open.
            data: Raw input text to write to the terminal.
            add_newline: Append a trailing newline to the input.
        """
        try:
            result = runtime.sessions.input(session_id, data, add_newline)
        except SessionError as exc:
            _audit(ctx, "shell_input", "error", {"session_id": session_id}, key=_key(ctx))
            return {"ok": False, "error": str(exc)}
        return result

    @mcp.tool()
    async def shell_output(
        session_id: str,
        since_seq: int = 0,
        limit_bytes: int = 262144,
        ctx: Context | None = None,
    ) -> dict:
        """Read new output from an interactive terminal session.

        Returns output produced after byte-cursor `since_seq` plus the next
        cursor value (`seq`). Poll repeatedly with the returned seq until
        you have everything. `truncated=true` means older output than
        since_seq was already discarded from the bounded buffer.

        Args:
            session_id: Session identifier from shell_open.
            since_seq: Byte cursor from a previous shell_output call (0 = all
                retained output).
            limit_bytes: Maximum bytes to return per call.
        """
        try:
            # Clamp at the tool boundary (B-8): negative limits previously
            # produced a backwards cursor and re-delivered old output.
            limit_bytes = max(0, min(int(limit_bytes), 8 * 1024 * 1024))
            return runtime.sessions.output(session_id, since_seq, limit_bytes)
        except SessionError as exc:
            return {"ok": False, "error": str(exc), "session_id": session_id}

    @mcp.tool()
    async def shell_resize(session_id: str, cols: int, rows: int, ctx: Context | None = None) -> dict:
        """Resize an interactive terminal session (SIGWINCH).

        Args:
            session_id: Session identifier from shell_open.
            cols: New terminal width in columns.
            rows: New terminal height in rows.
        """
        try:
            return runtime.sessions.resize(session_id, cols, rows)
        except SessionError as exc:
            return {"ok": False, "error": str(exc), "session_id": session_id}

    @mcp.tool()
    async def shell_close(session_id: str, ctx: Context | None = None) -> dict:
        """Terminate an interactive terminal session explicitly.

        Args:
            session_id: Session identifier from shell_open.
        """
        key = _key(ctx)
        try:
            result = await runtime.sessions.close(session_id)
            _audit(ctx, "shell_close", "ok", {"session_id": session_id}, key=key)
            return result
        except SessionError as exc:
            _audit(ctx, "shell_close", "error", {"session_id": session_id}, key=key)
            return {"ok": False, "error": str(exc), "session_id": session_id}

    @mcp.tool()
    async def shell_sessions(ctx: Context | None = None) -> dict:
        """List live interactive terminal sessions (for reconnection)."""
        return {
            "sessions": runtime.sessions.list(),
            "note": "reconnect by calling shell_output with a stored session_id",
        }

    # ==================================================================
    # Background jobs
    # ==================================================================
    @mcp.tool()
    async def job_status(job_id: str, ctx: Context | None = None) -> dict:
        """Get status metadata of a background job.

        Args:
            job_id: Job identifier from shell_exec(background=true).
        """
        try:
            job = runtime.jobs.status(job_id)
        except JobError as exc:
            return {"ok": False, "error": str(exc)}
        if not job:
            return {"ok": False, "error": f"unknown job_id: {job_id}"}
        return {"ok": True, **job}

    @mcp.tool()
    async def job_output(
        job_id: str,
        stream: str = "stdout",
        offset: int = 0,
        limit: int = 65536,
        ctx: Context | None = None,
    ) -> dict:
        """Read captured output of a background job (paginated).

        Args:
            job_id: Job identifier from shell_exec(background=true).
            stream: 'stdout' or 'stderr'.
            offset: Byte offset from a previous call (0 = from the start).
            limit: Maximum bytes to return per call.
        """
        try:
            return runtime.jobs.output(job_id, stream, offset, limit)
        except JobError as exc:
            return {"ok": False, "error": str(exc), "job_id": job_id}

    @mcp.tool()
    async def job_wait(job_id: str, timeout_seconds: float = 30.0, ctx: Context | None = None) -> dict:
        """Wait (bounded) for a background job to reach a final state.

        Args:
            job_id: Job identifier from shell_exec(background=true).
            timeout_seconds: Max seconds to wait before returning current
                status (server-side cap: 600; poll instead of holding one
                very long request open).
        """
        try:
            # Server-side bound (B-8): an unbounded/negative value must never
            # pin an HTTP worker indefinitely; agents poll instead.
            try:
                timeout_seconds = max(0.0, min(float(timeout_seconds), 600.0))
            except (TypeError, ValueError):
                timeout_seconds = 30.0
            job = await runtime.jobs.wait(job_id, timeout_seconds)
        except JobError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **job}

    @mcp.tool()
    async def job_cancel(job_id: str, ctx: Context | None = None) -> dict:
        """Cancel a running background job (SIGTERM then SIGKILL, process group).

        Args:
            job_id: Job identifier from shell_exec(background=true).
        """
        key = _key(ctx)
        try:
            job = await runtime.jobs.cancel(job_id)
            _audit(ctx, "job_cancel", "ok", {"job_id": job_id}, key=key)
            return {"ok": True, **job}
        except JobError as exc:
            _audit(ctx, "job_cancel", "error", {"job_id": job_id}, key=key)
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    async def job_list(limit: int = 50, ctx: Context | None = None) -> dict:
        """List recent background jobs (newest first).

        Args:
            limit: Maximum number of jobs to return.
        """
        return {"jobs": runtime.jobs.list(limit=limit)}

    # ==================================================================
    # server info
    # ==================================================================
    @mcp.tool()
    async def server_info(ctx: Context | None = None) -> dict:
        """Execution context, usage rules and operational defaults.

        Call this first to learn which host you are on, which user executes
        your commands, and any operator-configured advisory rules.
        """
        import platform

        return {
            "ok": True,
            "service": "UAEG",
            "version": __version__,
            "uptime_seconds": round(runtime.uptime_seconds, 1),
            "hostname": platform.node(),
            "exec_user": runtime.exec_user(),
            "platform": platform.platform(),
            "mcp_endpoint": runtime.mcp_url(),
            "usage_rules": runtime.usage_rules(),
            "defaults": {
                "shell": runtime.config.exec.shell,
                "default_cwd": os.path.expanduser(runtime.config.exec.default_cwd),
                "default_timeout_s": runtime.config.exec.default_timeout_s,
                "max_output_bytes_per_stream": runtime.config.exec.max_output_bytes,
                "pty_buffer_bytes": runtime.config.exec.pty_buffer_bytes,
                "max_sessions": runtime.config.exec.max_sessions,
            },
            "key": _key_summary(_key(ctx)),
        }
