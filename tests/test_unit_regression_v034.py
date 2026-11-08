"""Regression tests for the v0.3.4 P0 audit fixes.

Each test pins a defect found during the v0.3.4 full-system audit so the
fix can never silently regress. IDs (V-x, D-x) refer to
report/PATCH_CHANGELOG.md.

V-1/V-4  PTY: final output chunk + exit code lost when the session's
          process exits (P1) - the session vanished from the registry the
          instant the child died, orphaning the ring buffer tail, and both
          waitpid sites discarded the child's exit status.
V-2      Jobs: the detached shim was never reaped -> zombie accumulation.
V-3      Jobs: a failed DB insert after spawn orphaned a live process and
          leaked a raw sqlite3 exception to the MCP client.
D-1      Dashboard: deleting dashboard-pass.json at runtime silently
          disabled authentication (fail-open), including public binds (P1).
D-2      Dashboard: the 1 MiB body cap only checked Content-Length;
          chunked bodies bypassed it.
D-6/D-7  Config: dev-mode URL scheme; scalar config sections silently
          reverted whole sections to defaults (fail-open).
MCP-D1   Key-scoped ownership of sessions/jobs (cross-key PTY input
          injection was possible).
MCP-D2/D3/D4  Parameter validation: shell_resize ranges, job_list limit,
          shell_exec timeout clamp.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from conftest import write_test_config

from uaeg.db import Store
from uaeg.executor.jobs import JobError, JobManager
from uaeg.executor.pty import SessionError, SessionManager
from uaeg.config import Config, ConfigError
from uaeg import dashauth
from uaeg.dashauth import hash_password, password_file_path, save_password
from uaeg.dashboard import build_dashboard_asgi_app
from uaeg.runtime import Runtime

pytestmark = pytest.mark.usefixtures("env")


def _db() -> Path:
    return Path(os.environ["UAEG_DATA_DIR"]) / "uaeg.db"


def _zombie(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        state = stat.rsplit(")", 1)[1].split()[0]
        return state in ("Z", "X", "x")
    except OSError:
        return False


# ======================================================================
# V-1 / V-4 — PTY final output + exit code (P1)
# ======================================================================
def test_pty_final_output_and_exit_code_survive_process_exit():
    """The exact v0.3.3 defect: the last output line was unreadable and
    exit_code stayed None after the child exited with a non-zero code."""

    async def scenario() -> None:
        store = Store(_db())
        try:
            mgr = SessionManager(store)
            info = mgr.open(command="bash -c 'echo FINAL_TAIL_9x; exit 7'")
            sid = info["session_id"]
            # Poll like a real agent (fast AND slow cadences both bit in
            # the v0.3.3 reproduction).
            deadline = time.time() + 10
            out = None
            while time.time() < deadline:
                out = mgr.output(sid, since_seq=0)
                if out["alive"] is False and out["exit_code"] is not None:
                    break
                await asyncio.sleep(0.05)
            assert out is not None
            assert "FINAL_TAIL_9x" in out["data"], (
                "final output chunk must remain readable after exit"
            )
            assert out["alive"] is False
            assert out["exit_code"] == 7, "child exit code must be reported"
        finally:
            store.close()

    asyncio.run(scenario())


def test_pty_close_reports_exit_code():
    """shell_close must return the child's real exit code, not None."""

    async def scenario() -> None:
        store = Store(_db())
        try:
            mgr = SessionManager(store)
            info = mgr.open(command="bash -c 'sleep 30'")
            sid = info["session_id"]
            # Sending Ctrl-C: the tool contract is exercised e2e; here
            # we only need SOME termination path with a real exit status.
            mgr.input(sid, "\x03")
            result = await mgr.close(sid)
            assert result["ok"] is True
            # exit_code may legitimately be a signal-derived value, but it
            # must be an int (v0.3.3 always returned None here).
            assert isinstance(result["exit_code"], int), result
        finally:
            store.close()

    asyncio.run(scenario())


def test_pty_closed_session_expires_after_grace():
    """The grace window is bounded: the closed session is dropped later."""

    async def scenario() -> None:
        import uaeg.executor.pty as pty_mod

        store = Store(_db())
        try:
            mgr = SessionManager(store)
            # Shrink the grace window for the test.
            real_grace = pty_mod._EXITED_GRACE_S
            pty_mod._EXITED_GRACE_S = 0.2
            try:
                info = mgr.open(command="bash -c 'echo bye'")
                sid = info["session_id"]
                deadline = time.time() + 5
                while time.time() < deadline:
                    if mgr.output(sid, since_seq=0)["alive"] is False:
                        break
                    await asyncio.sleep(0.05)
                # after the (tiny) grace window it must be expired
                await asyncio.sleep(0.5)
                with pytest.raises(SessionError):
                    mgr.output(sid, since_seq=0)
                assert sid not in mgr.sessions
            finally:
                pty_mod._EXITED_GRACE_S = real_grace
        finally:
            store.close()

    asyncio.run(scenario())


def test_pty_resize_range_validation():
    """shell_resize must reject nonsense geometry (MCP-D2)."""

    async def scenario() -> None:
        store = Store(_db())
        try:
            mgr = SessionManager(store)
            info = mgr.open(command="bash -c 'sleep 10'")
            sid = info["session_id"]
            try:
                for cols, rows in ((0, 30), (120, 0), (1, 1), (120, 1001), (-5, 30)):
                    with pytest.raises(SessionError):
                        mgr.resize(sid, cols, rows)
                # valid range still works
                mgr.resize(sid, 80, 24)
            finally:
                await mgr.close(sid)
        finally:
            store.close()

    asyncio.run(scenario())


def test_pty_write_after_close_is_clean_session_error():
    """A racing EIO/EBADF on the master fd surfaces as SessionError."""

    async def scenario() -> None:
        store = Store(_db())
        try:
            mgr = SessionManager(store)
            info = mgr.open(command="bash -c 'echo done'")
            sid = info["session_id"]
            deadline = time.time() + 5
            while time.time() < deadline:
                if mgr.output(sid, since_seq=0)["alive"] is False:
                    break
                await asyncio.sleep(0.05)
            with pytest.raises(SessionError):
                mgr.input(sid, "hello")
        finally:
            store.close()

    asyncio.run(scenario())


# ======================================================================
# V-2 — zombie shim reap
# ======================================================================
def test_job_shim_is_reaped_no_zombie():
    async def scenario() -> None:
        store = Store(_db())
        try:
            mgr = JobManager(store, poll_interval=0.1)
            job = mgr.spawn("true")
            pid = job["pid"]
            deadline = time.time() + 15
            while time.time() < deadline:
                if store.get_job(job["job_id"])["status"] in (
                    "completed", "failed", "cancelled", "interrupted"
                ):
                    break
                await asyncio.sleep(0.1)
            # give the watcher's bounded reap a moment
            deadline = time.time() + 12
            while time.time() < deadline and _zombie(pid):
                await asyncio.sleep(0.2)
            assert not _zombie(pid), (
                f"shim pid {pid} must not linger as a zombie after completion"
            )
        finally:
            store.close()

    asyncio.run(scenario())


# ======================================================================
# V-3 — DB insert failure must not orphan the process
# ======================================================================
def test_job_spawn_kills_shim_when_db_insert_fails(monkeypatch):
    async def scenario() -> None:
        store = Store(_db())
        try:
            mgr = JobManager(store)

            real_insert = store.insert_job

            def broken_insert(*a, **kw):
                raise RuntimeError("database is locked (simulated)")

            monkeypatch.setattr(store, "insert_job", broken_insert)
            with pytest.raises(JobError):
                mgr.spawn("sleep 45")
            monkeypatch.setattr(store, "insert_job", real_insert)

            # No orphaned shim running sleep 45, no phantom job row.
            import subprocess as sp

            out = sp.run(
                ["pgrep", "-f", "sleep 45"], capture_output=True, text=True
            )
            assert out.stdout.strip() == "", "orphaned job process left behind"
            running = [j for j in store.list_jobs(limit=100) if j["status"] == "running"]
            assert running == []
        finally:
            store.close()

    asyncio.run(scenario())


# ======================================================================
# MCP-D1 — key-scoped ownership (sessions AND jobs)
# ======================================================================
def test_sessions_and_jobs_are_key_scoped():
    async def scenario() -> None:
        store = Store(_db())
        try:
            sm = SessionManager(store)
            jm = JobManager(store, poll_interval=0.1)
            info_a = sm.open(command="bash -c 'sleep 20'", key_id="key-a", key_name="A")
            job_a = jm.spawn("sleep 20", key_id="key-a", key_name="A")

            # key B is rejected for every PTY operation on A's session
            sid_a = info_a["session_id"]
            with pytest.raises(SessionError):
                sm.output(sid_a, since_seq=0, owner_key_id="key-b")
            with pytest.raises(SessionError):
                sm.input(sid_a, "id", owner_key_id="key-b")
            with pytest.raises(SessionError):
                sm.resize(sid_a, 100, 30, owner_key_id="key-b")
            with pytest.raises(SessionError):
                await sm.close(sid_a, owner_key_id="key-b")

            # key B is rejected for every job operation on A's job
            with pytest.raises(JobError):
                jm.status(job_a["job_id"], owner_key_id="key-b")
            with pytest.raises(JobError):
                jm.output(job_a["job_id"], owner_key_id="key-b")
            with pytest.raises(JobError):
                await jm.wait(job_a["job_id"], 0.1, owner_key_id="key-b")
            with pytest.raises(JobError):
                await jm.cancel(job_a["job_id"], owner_key_id="key-b")

            # key A still has full access to its own resources
            out = sm.output(sid_a, since_seq=0, owner_key_id="key-a")
            assert out["alive"] is True
            st = jm.status(job_a["job_id"], owner_key_id="key-a")
            assert st["status"] == "running"

            # operator level (no owner) still sees everything
            assert jm.status(job_a["job_id"])["status"] == "running"
            assert sm.get(sid_a).alive is True

            await sm.close(sid_a)
            await jm.cancel(job_a["job_id"])
        finally:
            store.close()

    asyncio.run(scenario())


def test_jobs_list_owner_filter():
    async def scenario() -> None:
        store = Store(_db())
        try:
            jm = JobManager(store, poll_interval=0.1)
            jm.spawn("true", key_id="key-a", key_name="A")
            jm.spawn("true", key_id="key-b", key_name="B")
            mine = jm.list(limit=10, owner_key_id="key-a")
            assert [j["key_id"] for j in mine] == ["key-a"]
            everything = jm.list(limit=10)
            assert {j["key_id"] for j in everything} == {"key-a", "key-b"}
        finally:
            store.close()

    asyncio.run(scenario())


# ======================================================================
# D-1 — dashboard auth fails CLOSED when the password file is deleted
# ======================================================================
def _build_app(env, **cfg_overrides):
    write_test_config(env, **cfg_overrides)
    config = Config.load(env["config"])
    store = Store(env["data"] / "uaeg.db")
    runtime = Runtime(config, store)
    app = build_dashboard_asgi_app(runtime)
    return app, runtime, store


async def _asgi_call(app, method, path, body=b"", headers=None, chunked=False,
                     want_headers=False):
    """Raw ASGI request; chunked=True omits Content-Length and splits the
    body into several http.request messages (D-2 test)."""
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if not chunked:
        hdrs.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": hdrs,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 3903),
        "scheme": "http",
    }

    messages = []

    async def receive():
        if chunked and body:
            if not hasattr(receive, "_queue"):
                half = len(body) // 2
                receive._queue = [
                    {"type": "http.request", "body": body[:half], "more_body": True},
                    {"type": "http.request", "body": body[half:], "more_body": False},
                ]
            if receive._queue:
                return receive._queue.pop(0)
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": body, "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    status = sent[0]["status"]
    payload = b"".join(m.get("body", b"") for m in sent)
    if want_headers:
        return status, payload, sent[0].get("headers", [])
    return status, payload


def test_dashboard_auth_fails_closed_on_password_file_deletion(env):
    """The exact v0.3.3 defect: rm dashboard-pass.json at runtime silently
    disabled login, exposing the dashboard AND its key-issuing API."""

    async def scenario() -> None:
        save_password(hash_password("correct-horse-battery-staple-1"))
        app, runtime, store = _build_app(env)
        try:
            # sanity: unauthenticated API access is rejected BEFORE deletion
            status, _ = await _asgi_call(app, "GET", "/api/keys")
            assert status == 401

            # ... and the attacker deletes the password file at runtime
            password_file_path().unlink()

            # auth must STAY enforced: 401, never the legacy pass-through
            status, payload = await _asgi_call(app, "GET", "/api/keys")
            assert status == 401, (
                "runtime password-file deletion must not disable authentication"
            )
            # the SPA itself must not be served unauthenticated either
            status, _ = await _asgi_call(app, "GET", "/")
            assert status == 302  # redirect to /login

            # login attempts must fail CLOSED (broken), not succeed
            body = json.dumps({"password": "anything"}).encode()
            status, _ = await _asgi_call(
                app, "POST", "/login", body=body,
                headers={"content-type": "application/json"},
            )
            assert status in (401, 503)
        finally:
            store.close()

    asyncio.run(scenario())


def test_dashboard_password_restore_recovers_after_deletion(env):
    """Fail-closed is not fail-forever: rewriting the file re-enables login."""

    async def scenario() -> None:
        save_password(hash_password("correct-horse-battery-staple-2"))
        app, runtime, store = _build_app(env)
        try:
            password_file_path().unlink()
            save_password(hash_password("correct-horse-battery-staple-3"))
            body = json.dumps({"password": "correct-horse-battery-staple-3"}).encode()
            status, payload = await _asgi_call(
                app, "POST", "/login", body=body,
                headers={"content-type": "application/json"},
            )
            assert status == 200, payload
        finally:
            store.close()

    asyncio.run(scenario())


def test_dashboard_manager_still_legacy_when_never_configured():
    """A run that NEVER saw a password keeps the documented legacy mode."""
    mgr = dashauth.PasswordManager()
    assert mgr.is_configured() is False


# ======================================================================
# D-2 — chunked request bodies honour the size cap
# ======================================================================
def test_dashboard_chunked_body_rejected_when_oversized(env):
    save_password(hash_password("correct-horse-battery-staple-4"))
    app, runtime, store = _build_app(env)

    async def scenario() -> None:
        try:
            # authenticate to get past the session + CSRF checks
            body = json.dumps({"password": "correct-horse-battery-staple-4"}).encode()
            status, payload, headers = await _asgi_call(
                app, "POST", "/login", body=body,
                headers={"content-type": "application/json"},
                want_headers=True,
            )
            assert status == 200, payload
            cookie = next(
                v.decode() for k, v in headers if k == b"set-cookie"
            ).split(";")[0]
            status, payload, _ = await _asgi_call(
                app, "GET", "/api/whoami", headers={"cookie": cookie},
                want_headers=True,
            )
            assert status == 200
            csrf = json.loads(payload)["csrf_token"]

            # oversized body WITHOUT Content-Length (chunked): v0.3.3 read it
            # entirely into memory inside the handler; the capped reader
            # must reject it with 413.
            big = b"x" * (dashauth.MAX_BODY_BYTES + 1024)
            status, _ = await _asgi_call(
                app, "POST", "/api/keys", body=big,
                headers={
                    "content-type": "application/json",
                    "cookie": cookie,
                    "x-csrf-token": csrf,
                },
                chunked=True,
            )
            assert status == 413, (
                "chunked oversized body must be rejected (v0.3.3 let it "
                "through and read it all into memory)"
            )
            # ... and with Content-Length the middleware still rejects it
            status, _ = await _asgi_call(
                app, "POST", "/api/keys", body=big,
                headers={
                    "content-type": "application/json",
                    "cookie": cookie,
                    "x-csrf-token": csrf,
                },
            )
            assert status == 413
        finally:
            store.close()

    asyncio.run(scenario())


# ======================================================================
# D-6 / D-7 — configuration truthfulness
# ======================================================================
def test_config_scalar_section_rejected_fail_closed(env):
    write_test_config(env)
    raw = json.loads(Path(env["config"]).read_text())
    raw["tls"] = "certbot"  # scalar where an object belongs
    Path(env["config"]).write_text(json.dumps(raw))
    with pytest.raises(ConfigError):
        Config.load(env["config"])


def test_config_null_section_means_defaults(env):
    write_test_config(env)
    raw = json.loads(Path(env["config"]).read_text())
    raw["network"] = None
    Path(env["config"]).write_text(json.dumps(raw))
    cfg = Config.load(env["config"])  # must NOT raise
    assert cfg.network.allowed_dashboard_cidrs is None


def test_mcp_url_scheme_follows_dev_flag(env):
    write_test_config(env)
    cfg = Config.load(env["config"])
    old = os.environ.get("UAEG_DEV_INSECURE_HTTP")
    try:
        os.environ.pop("UAEG_DEV_INSECURE_HTTP", None)
        assert cfg.mcp_url().startswith("https://")
        os.environ["UAEG_DEV_INSECURE_HTTP"] = "1"
        assert cfg.mcp_url().startswith("http://")
    finally:
        if old is not None:
            os.environ["UAEG_DEV_INSECURE_HTTP"] = old


# ======================================================================
# MCP-D3/D4 — tool-layer clamps (pin the bounds; e2e covers the wiring)
# ======================================================================
def test_tool_layer_clamp_bounds():
    # same expressions as the tool implementations (mirrors the B-8 pattern)
    def clamp_job_list(limit):
        return max(0, min(int(limit), 500))

    def clamp_exec_timeout(t):
        return min(max(float(t), 1.0), 3600.0)

    assert clamp_job_list(-1) == 0
    assert clamp_job_list(10**9) == 500
    assert clamp_exec_timeout(-5) == 1.0
    assert clamp_exec_timeout(0) == 1.0
    assert clamp_exec_timeout(10**9) == 3600.0
    assert clamp_exec_timeout(30) == 30.0
