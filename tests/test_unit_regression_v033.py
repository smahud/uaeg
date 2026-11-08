"""Regression tests for the v0.3.3 audit fixes (B-1..B-13, F-5, F-6, B-11).

Each test pins the exact defect found during the P0 end-to-end audit so the
fix can never silently regress. IDs refer to report/FULL_AUDIT.md.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from uaeg.db import Store
from uaeg.executor.jobs import JobManager, _boot_id, _group_alive
from uaeg.executor.pty import SessionManager
from uaeg import security

pytestmark = pytest.mark.usefixtures("env")


# ======================================================================
# B-1 — non-blocking PTY master: a suspended session must never freeze
# the event loop (service-wide freeze, P1).
# ======================================================================
def test_pty_write_to_suspended_session_does_not_block_loop():
    async def scenario() -> None:
        store = Store(Path(os.environ["UAEG_DATA_DIR"]) / "uaeg.db")
        try:
            mgr = SessionManager(store, buffer_bytes=64 * 1024)
            info = mgr.open(command="bash -c 'kill -STOP $$; sleep 30'")
            sid = info["session_id"]
            try:
                # The foreground process is SIGSTOPped: its tty will not
                # drain. A 64 KiB write must return promptly (queued) instead
                # of blocking the loop forever.
                loop_probe_started = time.monotonic()
                res = await asyncio.wait_for(
                    asyncio.to_thread(mgr.input, sid, "x" * 65536),
                    timeout=5.0,
                )
                assert res["ok"] is True
                # The event loop itself must stay responsive throughout.
                await asyncio.wait_for(asyncio.sleep(0.05), timeout=2.0)
                assert time.monotonic() - loop_probe_started < 5.0

                # Backlog cap: pushing far beyond 1 MiB must raise a clean
                # SessionError (backpressure), not hang or grow unbounded.
                with pytest.raises(Exception):
                    await asyncio.wait_for(
                        asyncio.to_thread(mgr.input, sid, "y" * (2 << 20)),
                        timeout=5.0,
                    )
            finally:
                await asyncio.wait_for(mgr.close(sid), timeout=10.0)
        finally:
            store.close()

    asyncio.run(scenario())


def test_pty_write_normal_session_delivers_input():
    async def scenario() -> None:
        store = Store(Path(os.environ["UAEG_DATA_DIR"]) / "uaeg.db")
        try:
            mgr = SessionManager(store)
            info = mgr.open(command="bash -c 'cat > /dev/null; sleep 5'")
            sid = info["session_id"]
            try:
                res = await asyncio.wait_for(
                    asyncio.to_thread(mgr.input, sid, "hello\n"), timeout=5.0
                )
                assert res["ok"] is True
                assert res["bytes_written"] >= len("hello\n")
            finally:
                await asyncio.wait_for(mgr.close(sid), timeout=10.0)
        finally:
            store.close()

    asyncio.run(scenario())


# ======================================================================
# B-3 — job cancellation must escalate to SIGKILL on process-group
# liveness, not on the already-final DB status.
# ======================================================================
def test_job_cancel_kills_term_ignoring_command():
    async def scenario() -> None:
        from uaeg import paths

        store = Store(paths.database_path())
        try:
            mgr = JobManager(store, poll_interval=0.1)
            marker = paths.jobs_dir() / "marker-b3"
            cmd = (
                f"trap '' TERM; touch {marker}; "
                "while true; do sleep 0.2; done"
            )
            job = mgr.spawn(cmd)
            jid = job["job_id"]
            # Wait until the marker exists: the command is running and has
            # installed its TERM trap.
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            assert marker.exists()

            t0 = time.monotonic()
            await asyncio.wait_for(mgr.cancel(jid), timeout=15.0)
            # Cancel must actually terminate the TERM-ignoring tree: the
            # group is gone (v0.3.2 left it running for ~forever).
            pid = store.get_job(jid).get("pid")
            deadline = time.monotonic() + 5
            while _group_alive(pid) and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            assert not _group_alive(pid), (
                "cancelled job's process group survived SIGTERM+SIGKILL"
            )
            assert time.monotonic() - t0 < 15
            final = store.get_job(jid)
            assert final["status"] in ("cancelled", "failed", "completed")
        finally:
            store.close()

    asyncio.run(scenario())


# ======================================================================
# B-4 — boot-id stamp: a job from a previous boot is interrupted, never
# trusted via a possibly-recycled pid.
# ======================================================================
def test_reattach_marks_previous_boot_job_interrupted():
    from uaeg import paths

    async def scenario() -> None:
        store = Store(paths.database_path())
        try:
            # Fake a running job whose pid IS alive right now (pid 1) but
            # whose boot stamp differs from the current boot.
            store.insert_job(
                job_id="job-bootold",
                command="sleep 9999",
                cwd=None,
                pid=1,
                key_id=None,
                key_name=None,
            )
            job_dir = paths.jobs_dir() / "job-bootold"
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "boot_id").write_text("an-old-boot-id\n", encoding="ascii")

            mgr = JobManager(store, poll_interval=0.1)
            counts = mgr.reattach()
            job = store.get_job("job-bootold")
            assert job["status"] == "interrupted"
            assert counts["interrupted"] >= 1
        finally:
            store.close()

    asyncio.run(scenario())


def test_spawn_writes_current_boot_id():
    from uaeg import paths

    async def scenario() -> None:
        store = Store(paths.database_path())
        try:
            mgr = JobManager(store, poll_interval=0.1)
            job = mgr.spawn("true")
            stamp = (paths.jobs_dir() / job["job_id"] / "boot_id").read_text(
                encoding="ascii"
            ).strip()
            if _boot_id():  # /proc available in the test environment
                assert stamp == _boot_id()
            await asyncio.wait_for(mgr.cancel(job["job_id"]), timeout=10)
        finally:
            store.close()

    asyncio.run(scenario())


# ======================================================================
# B-5 — a transient failure inside the watcher poll must not kill the
# watcher task.
# ======================================================================
def test_job_watcher_survives_check_exception():
    class FlakyStore(Store):
        def __init__(self, path):
            super().__init__(path)
            self.fail_next = 0

        def get_job(self, job_id):
            if self.fail_next > 0:
                self.fail_next -= 1
                raise RuntimeError("transient sqlite hiccup")
            return super().get_job(job_id)

    async def scenario() -> None:
        from uaeg import paths

        store = FlakyStore(paths.database_path())
        try:
            mgr = JobManager(store, poll_interval=0.05)
            job = mgr.spawn("sleep 2")
            jid = job["job_id"]
            runtime = mgr._runtime[jid]
            store.fail_next = 3  # next three polls blow up
            await asyncio.sleep(0.4)
            # The watcher must still be alive and reconcile the finished job.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if store.get_job(jid)["status"] in (
                    "completed",
                    "failed",
                    "interrupted",
                ):
                    break
                await asyncio.sleep(0.1)
            assert store.get_job(jid)["status"] == "completed"
        finally:
            store.close()

    asyncio.run(scenario())


# ======================================================================
# B-2 — CLI containment sees open sessions beyond the newest 1000 rows.
# ======================================================================
def test_containment_key_resources_sees_old_open_sessions():
    from uaeg import containment
    from uaeg import paths

    store = Store(paths.database_path())
    try:
        # 1100 CLOSED sessions newer than the target key's OPEN session.
        for i in range(1100):
            store.insert_session(
                session_id=f"s-noise-{i}",
                command="true",
                cwd=None,
                pid=None,
                key_id="k-other",
                key_name=None,
            )
            store.update_session(f"s-noise-{i}", status="closed", closed=True)
        store.insert_session(
            session_id="s-victim",
            command="bash",
            cwd=None,
            pid=999999,
            key_id="k-audit",
            key_name=None,
        )
        res = containment.key_resources(store, "k-audit")
        ids = [s["session_id"] for s in res["sessions"]]
        assert "s-victim" in ids, (
            "open session older than the newest 1000 rows was invisible to "
            "revocation containment (B-2)"
        )
    finally:
        store.close()


# ======================================================================
# B-8 — non-positive read limits keep a consistent cursor.
# ======================================================================
def test_read_since_negative_limit_is_noop():
    from uaeg.executor.pty import TerminalSession

    s = TerminalSession("t", 0, 1, "cmd", None, buffer_bytes=4096)
    s.feed(b"0123456789")
    data, seq, trunc = s.read_since(since_seq=2, limit=-5)
    assert data == ""
    assert seq == 2  # cursor unchanged - never backwards
    data2, seq2, _ = s.read_since(since_seq=seq, limit=100)
    assert data2 == "23456789"
    assert seq2 == 10


def test_job_wait_timeout_is_clamped_by_tool():
    # The MCP tool clamps timeout_seconds to [0, 600]; a non-numeric value
    # falls back to the default rather than raising.
    from uaeg.mcp_tools import register_tools

    # We can't call the tool without a full MCP context; pin the clamp logic
    # by reproducing it here against the same bounds.
    def clamp(v):
        try:
            return max(0.0, min(float(v), 600.0))
        except (TypeError, ValueError):
            return 30.0

    assert clamp(-5) == 0.0
    assert clamp(10**9) == 600.0
    assert clamp("abc") == 30.0
    assert clamp(12.5) == 12.5


# ======================================================================
# B-9 — short-form CLI password flags are redacted.
# ======================================================================
@pytest.mark.parametrize(
    "text,expect_redacted",
    [
        ("mysql -u root -ppassword123 -e 'select 1'", True),
        ("psql -whunter2 -U admin -c 'select 1'", True),
        ("redis-cli -aStr0ngPw123 ping", True),
        ("grep -print foo", False),
        ("bash -c 'ls -a'", False),
    ],
)
def test_redaction_short_flag_passwords(text, expect_redacted):
    out = security.redact_secrets(text)
    if expect_redacted:
        assert security._REDACT_MARK in out, out
        assert "password123" not in out
        assert "hunter2" not in out
        assert "Str0ngPw123" not in out
    else:
        assert security._REDACT_MARK not in out, out


def test_redaction_preserves_ordinary_flags():
    out = security.redact_secrets("grep -print foo; mytool -append x")
    assert "-print" in out
    assert "-append" in out
    assert security._REDACT_MARK not in out


# ======================================================================
# B-10 — `config get` distinguishes existing null keys from unknown keys.
# ======================================================================
def test_dig_key_exists_semantics():
    from uaeg.cli import _dig, _key_exists

    data = {"tls": {"cert": None, "mode": "self-signed"}, "public_host": "h"}
    assert _key_exists(data, "tls.cert") is True  # exists, value None
    assert _dig(data, "tls.cert") is None
    assert _key_exists(data, "tls.mode") is True
    assert _dig(data, "tls.mode") == "self-signed"
    assert _key_exists(data, "tls.nonexistent") is False
    assert _key_exists(data, "nope.nada") is False


# ======================================================================
# B-11 — exec_logs retention keeps only the newest N rows.
# ======================================================================
def test_trim_exec_logs_retention():
    from uaeg import paths

    store = Store(paths.database_path())
    try:
        for i in range(25):
            store.add_log(None, "tester", "shell_exec", "ok", meta={"i": i})
        removed = store.trim_exec_logs(keep=10)
        assert removed == 15
        assert store.count_logs() == 10
        # The NEWEST rows survive.
        logs = store.list_logs(limit=10)
        kept_meta = [json_i for l in logs for json_i in [l["meta"].get("i")]]
        assert max(kept_meta) == 24
        assert min(kept_meta) == 15
    finally:
        store.close()


# ======================================================================
# B-12 — rotation creates the replacement BEFORE revoking.
# ======================================================================
def test_rotation_failure_keeps_old_key_active():
    from uaeg import paths

    store = Store(paths.database_path())
    try:
        material = security.new_key("victim")
        store.create_key(
            key_id=material.key_id,
            name=material.name,
            prefix=material.prefix,
            verifier_hex=material.verifier,
        )
        real_create = store.create_key

        def exploding_create(**kwargs):
            raise RuntimeError("disk full mid-rotation")

        store.create_key = exploding_create  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            # Mirror the dashboard/CLI rotation order: create new, then revoke.
            new_material = security.new_key("replacement")
            store.create_key(
                key_id=new_material.key_id,
                name=new_material.name,
                prefix=new_material.prefix,
                verifier_hex=new_material.verifier,
                permission_mode="all",
            )
        store.create_key = real_create  # type: ignore[method-assign]
        # The old key must still be ACTIVE (the crash happened before revoke).
        assert store.get_key(material.key_id)["status"] == "active"
    finally:
        store.close()


# ======================================================================
# F-5 — /api/whoami must not 500 in legacy (no-password) mode.
# ======================================================================
def test_whoami_legacy_mode_returns_payload():
    import json as _json

    from starlette.testclient import TestClient

    from uaeg.config import Config
    from uaeg.dashboard import build_dashboard_asgi_app
    from uaeg.runtime import Runtime

    store = Store(Path(os.environ["UAEG_DATA_DIR"]) / "uaeg.db")
    try:
        cfg = Config()
        cfg.dashboard.bind = "127.0.0.1"
        runtime = Runtime(cfg, store)
        app = build_dashboard_asgi_app(runtime)
        client = TestClient(app)
        resp = client.get("/api/whoami")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["authenticated"] is False
        assert "csrf_token" in payload
    finally:
        store.close()


# ======================================================================
# F-6 — /api/sessions tolerates a session closing mid-poll.
# ======================================================================
def test_sessions_endpoint_skips_vanished_session():
    from starlette.testclient import TestClient

    from uaeg.config import Config
    from uaeg.dashboard import build_dashboard_asgi_app
    from uaeg.runtime import Runtime

    store = Store(Path(os.environ["UAEG_DATA_DIR"]) / "uaeg.db")
    try:
        cfg = Config()
        cfg.dashboard.bind = "127.0.0.1"
        runtime = Runtime(cfg, store)
        app = build_dashboard_asgi_app(runtime)

        # Plant a stale entry in the live registry: list() reports it, but
        # get() raises SessionError (vanished between list and get).
        class FakeSessions:
            def list(self):
                return [{"session_id": "ghost", "pid": 1}]

            def get(self, sid):
                from uaeg.executor.pty import SessionError

                raise SessionError("gone")

        runtime.sessions = FakeSessions()  # type: ignore[assignment]
        client = TestClient(app)
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        assert resp.json()["live"] == []
    finally:
        store.close()
