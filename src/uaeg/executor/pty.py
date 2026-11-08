"""Persistent interactive terminal sessions over a native PTY (PRD FR-008).

Each session is a real pseudo-terminal (``pty.openpty``) with a session-leader
shell attached, so agents get genuine interactive behavior: line editing,
control characters (e.g. "\\x03" for Ctrl-C), job control, SIGWINCH resizing.

Design points:
- Output is accumulated in a bounded in-memory ring buffer (default 256 KiB)
  with a monotonically increasing byte sequence number. Clients poll with
  ``shell_output(session_id, since_seq=N)`` and receive everything after N;
  the response carries the next cursor. This makes reconnection trivial and
  keeps memory bounded (PRD section 14).
- Sessions live in the server process. If the server restarts, the PTY master
  closes, the shell receives SIGHUP, and sessions are marked interrupted.
- Session IDs are UUID4 hex (collision-safe).
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import pty
import signal
import struct
import termios
import threading
import time
import uuid
import warnings
from typing import Any, Callable

from .. import security as _security
from ..db import Store

log = logging.getLogger("uaeg.pty")

_READ_CHUNK = 65536

# Hard cap on queued (undelivered) input bytes per session. When a session's
# foreground process is suspended (Ctrl-Z) or simply never reads its tty, the
# kernel tty buffer fills; writes to the master then return EAGAIN. Queued
# input beyond this cap is rejected so a stalled session cannot grow without
# bound (mirrors the sync-exec output cap of 1 MiB).
_MAX_PENDING_INPUT = 1 << 20


class SessionError(Exception):
    pass


class TerminalSession:
    def __init__(
        self,
        session_id: str,
        master_fd: int,
        pid: int,
        command: str,
        cwd: str | None,
        buffer_bytes: int,
        key_id: str | None = None,
    ):
        self.session_id = session_id
        self.master_fd = master_fd
        self.pid = pid
        self.command = command
        self.cwd = cwd
        self.key_id = key_id
        self.created_at = time.time()
        self.last_activity = time.time()
        self.closed = False
        self.closed_at: float | None = None
        self.exit_code: int | None = None

        self._buffer = bytearray()
        self._buffer_cap = buffer_bytes
        self._total_bytes = 0  # monotonic sequence of output bytes
        self._reaper: asyncio.Task | None = None

        # B-1: the master fd is kept O_NONBLOCK; input that cannot be delivered
        # immediately (suspended foreground process, full tty buffer) is queued
        # here and drained by the event loop when the tty becomes writable.
        self._pending = bytearray()
        self._drain_armed = False
        # Serializes the closed check-then-set + fd swap across the event-loop
        # thread and to_thread termination workers (B-7).
        self._close_lock = threading.Lock()

    # ------------------------------------------------------------------
    @property
    def base_seq(self) -> int:
        """Oldest sequence number still retained in the ring buffer."""
        return self._total_bytes - len(self._buffer)

    @property
    def alive(self) -> bool:
        return not self.closed

    def feed(self, data: bytes) -> None:
        self._total_bytes += len(data)
        self._buffer += data
        if len(self._buffer) > self._buffer_cap:
            del self._buffer[: len(self._buffer) - self._buffer_cap]
        self.last_activity = time.time()

    def read_since(self, since_seq: int, limit: int = 262_144) -> tuple[str, int, bool]:
        """Return (data, next_seq, truncated)."""
        start = max(since_seq, self.base_seq)
        if start > self._total_bytes:
            start = self._total_bytes
        # A non-positive limit reads nothing but keeps the cursor consistent
        # (B-8): previously a negative limit produced a backwards cursor and
        # re-delivered old bytes on the next poll.
        if limit is None or limit <= 0:
            return "", start, since_seq < self.base_seq
        end = min(start + limit, self._total_bytes)
        chunk = bytes(self._buffer[start - self.base_seq : end - self.base_seq])
        truncated = since_seq < self.base_seq
        return chunk.decode("utf-8", errors="replace"), end, truncated

    def write(self, data: bytes) -> int:
        """Queue input for delivery to the session's tty.

        The master fd is non-blocking (B-1): a suspended foreground process
        or a full tty buffer makes os.write return EAGAIN or a short count.
        The remainder is queued and flushed by the SessionManager's write
        drain when the tty becomes writable, so the event loop is never
        blocked. Returns the number of bytes accepted for delivery.
        """
        if self.closed or self.master_fd < 0:
            raise SessionError("session is closed")
        if not data:
            return 0
        if self._pending:
            # Previous input has not been consumed by the tty yet; keep order.
            self._pending += data
            accepted = len(data)
        else:
            try:
                accepted = os.write(self.master_fd, data)
            except BlockingIOError:
                accepted = 0
            if accepted < len(data):
                self._pending += data[accepted:]
                accepted = len(data)
        if len(self._pending) > _MAX_PENDING_INPUT:
            raise SessionError(
                "session input backlog exceeds "
                f"{_MAX_PENDING_INPUT} bytes: the session's foreground process "
                "is not reading its terminal (suspended with Ctrl-Z, or stalled). "
                "Resume the process, or close the session."
            )
        self.last_activity = time.time()
        return accepted

    def resize(self, cols: int, rows: int) -> None:
        if self.closed or self.master_fd < 0:
            raise SessionError("session is closed")
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        try:
            os.killpg(self.pid, signal.SIGWINCH)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def info(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "command": self.command,
            "cwd": self.cwd,
            "pid": self.pid,
            "key_id": self.key_id,
            "alive": self.alive,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "seq_total": self._total_bytes,
            "buffered_bytes": len(self._buffer),
            "buffer_base_seq": self.base_seq,
            "exit_code": self.exit_code,
        }

    def preview_tail(self, n: int = 512) -> str:
        return bytes(self._buffer[-n:]).decode("utf-8", errors="replace")


class SessionManager:
    def __init__(
        self,
        store: Store,
        shell: str = "/bin/bash",
        buffer_bytes: int = 262_144,
        max_sessions: int = 16,
    ):
        self.store = store
        self.shell = shell
        self.buffer_bytes = buffer_bytes
        self.max_sessions = max_sessions
        self.sessions: dict[str, TerminalSession] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    def open(
        self,
        command: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = 120,
        rows: int = 30,
        key_id: str | None = None,
        key_name: str | None = None,
    ) -> dict[str, Any]:
        if len(self.sessions) >= self.max_sessions:
            raise SessionError(
                f"session limit reached ({self.max_sessions}); close unused "
                "sessions with shell_close first"
            )
        if not (2 <= cols <= 1000 and 2 <= rows <= 1000):
            raise SessionError("cols and rows must be between 2 and 1000")
        if not os.path.exists(self.shell):
            raise SessionError(f"configured shell not found: {self.shell}")
        if cwd and not os.path.isdir(cwd):
            raise SessionError(f"cwd is not a directory: {cwd}")

        shell = self.shell
        argv = [shell]
        if command:
            argv += ["-c", command]

        session_id = uuid.uuid4().hex
        child_env = os.environ.copy()
        child_env.setdefault("TERM", "xterm-256color")
        child_env["UAEG_SESSION_ID"] = session_id
        for k, v in (env or {}).items():
            child_env[k] = str(v)

        loop = self._loop or asyncio.get_running_loop()
        self._loop = loop

        master_fd, slave_fd = pty.openpty()
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        os.set_inheritable(master_fd, False)
        # B-1: never let a write to the master block the event loop. A session
        # whose foreground process is suspended (agent sends Ctrl-Z) stops
        # draining its tty; with a blocking master, os.write would freeze the
        # whole service. Non-blocking + a write drain keeps the loop live.
        fcntl.fcntl(
            master_fd,
            fcntl.F_SETFL,
            fcntl.fcntl(master_fd, fcntl.F_GETFL) | os.O_NONBLOCK,
        )

        # fork+immediate-exec is the standard PTY pattern (same as pexpect /
        # subprocess with preexec_fn). The child only calls raw syscalls before
        # exec, so the multithreaded-fork deprecation warning is safe to ignore.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            pid = os.fork()
        if pid == 0:
            # ---- child: become the session-leader shell on this PTY ----
            try:
                os.close(master_fd)
                os.setsid()
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)
                if cwd:
                    os.chdir(cwd)
                os.execve(argv[0], argv, child_env)
            except BaseException:  # noqa: BLE001 - never propagate from child
                os._exit(127)
            os._exit(127)  # pragma: no cover

        # ---- parent ----
        os.close(slave_fd)
        session = TerminalSession(
            session_id=session_id,
            master_fd=master_fd,
            pid=pid,
            command=command or f"{shell} (interactive)",
            cwd=cwd,
            buffer_bytes=self.buffer_bytes,
            key_id=key_id,
        )
        self.sessions[session_id] = session

        loop.add_reader(master_fd, self._on_readable, session)
        session._reaper = loop.create_task(
            self._reap_loop(session), name=f"uaeg-pty-reap-{session_id[:8]}"
        )

        self.store.insert_session(
            session_id=session_id,
            command=command or f"{shell} (interactive)",
            cwd=cwd,
            pid=pid,
            key_id=key_id,
            key_name=key_name,
        )
        log.info(
            "session %s opened (pid %d, %s)",
            session_id[:12],
            pid,
            _security.redact_secrets(command or "interactive shell"),
        )
        return {
            "session_id": session_id,
            "pid": pid,
            "command": session.command,
            "cols": cols,
            "rows": rows,
            "note": "poll output with shell_output(session_id, since_seq=0); "
            "send input with shell_input(session_id, data)",
        }

    # ------------------------------------------------------------------
    def get(self, session_id: str) -> TerminalSession:
        session = self.sessions.get(session_id)
        if not session:
            raise SessionError(
                f"unknown or expired session_id: {session_id}; list live "
                "sessions with shell_sessions()"
            )
        return session

    def output(self, session_id: str, since_seq: int = 0, limit: int = 262_144) -> dict[str, Any]:
        session = self.get(session_id)
        data, next_seq, truncated = session.read_since(since_seq, limit)
        return {
            "session_id": session_id,
            "data": data,
            "seq": next_seq,
            "truncated": truncated,
            "alive": session.alive,
            "pid": session.pid,
            "exit_code": session.exit_code,
        }

    def input(self, session_id: str, data: str, add_newline: bool = False) -> dict[str, Any]:
        session = self.get(session_id)
        payload = data + ("\n" if add_newline else "")
        written = session.write(payload.encode("utf-8"))
        if session._pending:
            self._arm_input_drain(session)
        self.store.update_session(session_id, touch=True)
        return {
            "session_id": session_id,
            "bytes_written": written,
            "bytes_queued": len(session._pending),
            "ok": True,
        }

    def resize(self, session_id: str, cols: int, rows: int) -> dict[str, Any]:
        session = self.get(session_id)
        session.resize(cols, rows)
        return {"session_id": session_id, "cols": cols, "rows": rows, "ok": True}

    def list(self) -> list[dict[str, Any]]:
        # Snapshot: termination workers mutate self.sessions from other
        # threads; iterating the live dict can raise RuntimeError (B-6).
        return [s.info() for s in list(self.sessions.values())]

    # ------------------------------------------------------------------
    # Input drain (B-1): flush queued input when the tty becomes writable.
    # ------------------------------------------------------------------
    def _arm_input_drain(self, session: TerminalSession) -> None:
        if session._drain_armed or session.closed:
            return
        loop = self._loop
        fd = session.master_fd
        if loop is None or fd < 0:
            return
        session._drain_armed = True
        try:
            loop.add_writer(fd, self._drain_input, session)
        except (RuntimeError, ValueError, NotImplementedError, OSError) as exc:
            session._drain_armed = False
            log.warning("could not arm input drain for %s: %s", session.session_id[:12], exc)

    def _drain_input(self, session: TerminalSession) -> None:
        fd = session.master_fd
        if session.closed or fd < 0:
            self._disarm_input_drain(session)
            return
        try:
            n = os.write(fd, bytes(session._pending))
        except BlockingIOError:
            return  # still stalled; the writer stays armed
        except OSError:
            self._disarm_input_drain(session)
            return
        if n > 0:
            del session._pending[:n]
        if not session._pending:
            self._disarm_input_drain(session)

    def _disarm_input_drain(self, session: TerminalSession) -> None:
        session._pending.clear()
        if not session._drain_armed:
            return
        session._drain_armed = False
        loop = self._loop
        fd = session.master_fd
        if loop is not None and fd >= 0:
            try:
                loop.remove_writer(fd)
            except (RuntimeError, ValueError, NotImplementedError, OSError):
                pass

    async def close(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        await asyncio.to_thread(self._terminate, session)
        return {
            "session_id": session_id,
            "ok": True,
            "status": "closed",
            "exit_code": session.exit_code,
        }

    async def close_by_key(self, key_id: str) -> int:
        """Revocation containment: terminate every live session owned by a
        key. Returns the number of sessions terminated."""
        count = 0
        for session in list(self.sessions.values()):
            if session.key_id != key_id:
                continue
            try:
                await self.close(session.session_id)
                count += 1
            except SessionError:
                pass
        return count

    async def terminate_by_id(self, session_id: str) -> dict[str, Any]:
        return await self.close(session_id)

    # ------------------------------------------------------------------
    def _on_readable(self, session: TerminalSession) -> None:
        try:
            data = os.read(session.master_fd, _READ_CHUNK)
        except BlockingIOError:
            # Master is O_NONBLOCK (B-1): a spurious readability event with
            # no data left is a no-op, not an exit.
            return
        except OSError:
            self._mark_exited(session)
            return
        if not data:
            self._mark_exited(session)
            return
        session.feed(data)

    async def _reap_loop(self, session: TerminalSession) -> None:
        try:
            while True:
                await asyncio.sleep(0.4)
                try:
                    pid, status = os.waitpid(session.pid, os.WNOHANG)
                except ChildProcessError:
                    return
                if pid == session.pid:
                    try:
                        session.exit_code = os.waitstatus_to_exitcode(status)
                    except ValueError:
                        session.exit_code = None
                    # Drain any final output, then close.
                    await asyncio.sleep(0.15)
                    self._drain_and_close(session)
                    return
                if session.closed:
                    # The PTY master is gone, so the child received SIGHUP.
                    # Give it a short grace period, then force-kill so it can
                    # neither linger nor become an unreaped zombie.
                    closed_for = time.monotonic() - (session.closed_at or time.monotonic())
                    if closed_for > 5.0:
                        try:
                            os.killpg(session.pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
        except asyncio.CancelledError:
            pass

    def _drain_and_close(self, session: TerminalSession) -> None:
        while True:
            try:
                data = os.read(session.master_fd, _READ_CHUNK)
            except OSError:
                break
            if not data:
                break
            session.feed(data)
        self._mark_exited(session)

    def _mark_exited(self, session: TerminalSession) -> None:
        # Atomic closed check-then-set + fd swap (B-7): the event-loop reader
        # callback and the to_thread termination worker can both observe
        # closed=False; without the lock both would close the same fd, and a
        # recycled fd number would kill an unrelated descriptor.
        with session._close_lock:
            if session.closed:
                return
            session.closed = True
            session.closed_at = time.monotonic()
            fd = session.master_fd
            session.master_fd = -1
            session._drain_armed = False

        loop = self._loop

        def _close_fd() -> None:
            if loop is not None and fd >= 0:
                try:
                    loop.remove_reader(fd)
                except (RuntimeError, ValueError, NotImplementedError, OSError):
                    pass
                try:
                    loop.remove_writer(fd)
                except (RuntimeError, ValueError, NotImplementedError, OSError):
                    pass
            try:
                os.close(fd)
            except OSError:
                pass
            session._pending.clear()

        # remove_reader/os.close must happen on the loop thread (or when the
        # loop is already gone, e.g. during test teardown).
        if loop is not None and loop.is_running():
            in_loop = False
            try:
                in_loop = asyncio.get_running_loop() is loop
            except RuntimeError:
                in_loop = False
            if in_loop:
                _close_fd()
            else:
                loop.call_soon_threadsafe(_close_fd)
        else:
            _close_fd()

        # Reap immediately when possible; if the child has not exited yet
        # the reaper task stays alive to collect it (its loop keeps running
        # after closed) - that prevents zombie children.
        reaped = False
        try:
            pid, _ = os.waitpid(session.pid, os.WNOHANG)
            reaped = pid == session.pid
        except (ChildProcessError, OSError):
            reaped = True  # already collected elsewhere
        if session._reaper and not session._reaper.done() and reaped:
            try:
                session._reaper.cancel()
            except RuntimeError:  # event loop already closed
                pass
        self.store.update_session(session.session_id, status="closed", closed=True)
        self.sessions.pop(session.session_id, None)
        log.info(
            "session %s closed (exit %s)", session.session_id[:12], session.exit_code
        )

    def _terminate(self, session: TerminalSession) -> None:
        """Synchronous, potentially blocking termination (run in a thread)."""
        if session.closed:
            return
        try:
            os.killpg(session.pid, signal.SIGHUP)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and not session.closed:
            try:
                pid, _ = os.waitpid(session.pid, os.WNOHANG)
                if pid == session.pid:
                    break
            except ChildProcessError:
                break
            time.sleep(0.05)
        try:
            os.killpg(session.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            os.waitpid(session.pid, 0)
        except (ChildProcessError, OSError):
            pass
        self._drain_and_close(session)

    # ------------------------------------------------------------------
    def startup_reconcile(self) -> int:
        """After a server restart, previous PTYs are gone (SIGHUP'd)."""
        count = len(self.store.open_sessions())
        if count:
            self.store.mark_open_sessions("interrupted")
            log.warning(
                "%d session(s) from a previous server run marked interrupted", count
            )
        return count

    async def shutdown(self) -> None:
        sessions = list(self.sessions.values())
        for session in sessions:
            await asyncio.to_thread(self._terminate, session)
        # _terminate reaps the children; cancel any reapers still pending so
        # the event loop is not left with dangling tasks.
        for session in sessions:
            if session._reaper and not session._reaper.done():
                session._reaper.cancel()
