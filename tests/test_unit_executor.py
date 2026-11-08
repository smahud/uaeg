"""Unit tests: sync runner, background jobs, PTY sessions."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from uaeg.db import Store
from uaeg.executor.jobs import JobManager
from uaeg.executor.pty import SessionManager
from uaeg.executor.runner import run_command


# ----------------------------------------------------------------------
# runner
# ----------------------------------------------------------------------
def test_run_command_basic():
    res = asyncio.run(run_command("echo hello && echo err >&2", timeout_s=15))
    assert res.exit_code == 0
    assert "hello" in res.stdout
    assert "err" in res.stderr
    assert not res.timed_out


def test_run_command_exit_code_and_env_cwd(tmp_path):
    res = asyncio.run(
        run_command(
            "echo $MYVAR; pwd; exit 7",
            cwd=str(tmp_path),
            env={"MYVAR": "uaeg-test"},
            timeout_s=15,
        )
    )
    assert res.exit_code == 7
    assert "uaeg-test" in res.stdout
    assert str(tmp_path) in res.stdout


def test_run_command_timeout():
    res = asyncio.run(run_command("sleep 5", timeout_s=1))
    assert res.timed_out
    assert res.exit_code is None
    assert "timed out" in (res.error or "")


def test_run_command_output_cap():
    res = asyncio.run(
        run_command("yes hello | head -c 200000", timeout_s=20, max_output_bytes=10000)
    )
    assert res.stdout_bytes >= 200000
    assert len(res.stdout) <= 10100
    assert res.stdout_truncated


# ----------------------------------------------------------------------
# jobs
# ----------------------------------------------------------------------
@pytest.fixture()
def job_env(env):
    from uaeg import paths

    store = Store(paths.database_path())
    manager = JobManager(store)
    yield store, manager
    asyncio.run(manager.shutdown())
    store.close()


def test_job_lifecycle(job_env):
    store, manager = job_env

    async def scenario():
        job = manager.spawn("echo done", key_id="k-1", key_name="t")
        job_id = job["job_id"]
        final = await manager.wait(job_id, timeout_s=15)
        assert final["status"] == "completed"
        assert final["exit_code"] == 0
        out = manager.output(job_id, "stdout")
        assert "done" in out["data"]
        assert out["offset"] == len(out["data"].encode())

    asyncio.run(scenario())


def test_job_failure_recorded(job_env):
    store, manager = job_env

    async def scenario():
        job = manager.spawn("echo oops >&2; exit 3")
        final = await manager.wait(job["job_id"], timeout_s=15)
        assert final["status"] == "failed"
        assert final["exit_code"] == 3
        err = manager.output(job["job_id"], "stderr")
        assert "oops" in err["data"]

    asyncio.run(scenario())


def test_job_cancel(job_env):
    store, manager = job_env

    async def scenario():
        job = manager.spawn("sleep 30")
        assert job["status"] == "running"
        cancelled = await manager.cancel(job["job_id"])
        assert cancelled["status"] == "cancelled"
        await asyncio.sleep(0.3)
        assert store.get_job(job["job_id"])["status"] == "cancelled"

    asyncio.run(scenario())


def test_job_survives_manager_shutdown(job_env):
    """Detached jobs keep running even when the manager stops watching."""
    store, manager = job_env
    from uaeg import paths

    async def scenario():
        job = manager.spawn("sleep 2 && echo late")
        job_dir = paths.jobs_dir() / job["job_id"]
        await asyncio.sleep(0.5)
        await manager.shutdown()  # simulates server stop (no kill)
        await asyncio.sleep(0.3)
        # process still alive: exit file not yet written
        assert not (job_dir / "exit").exists()
        await asyncio.sleep(2.5)
        # shim finished while manager was down: exit file persisted
        assert (job_dir / "exit").read_text().strip() == "0"

    asyncio.run(scenario())


def test_job_reattach_after_restart(job_env):
    """A running job survives a manager restart and gets finalized."""
    store, manager = job_env
    from uaeg import paths

    async def scenario():
        job = manager.spawn("sleep 2 && echo survived")
        job_id = job["job_id"]
        await asyncio.sleep(0.4)
        await manager.shutdown()

        # New manager instance = new server process
        manager2 = JobManager(store)
        counts = manager2.reattach()
        assert counts["running"] == 1
        final = await manager2.wait(job_id, timeout_s=15)
        assert final["status"] == "completed"
        assert final["exit_code"] == 0
        await manager2.shutdown()

    asyncio.run(scenario())


def test_job_interrupted_after_reboot_like_kill(job_env):
    """Dead pid + no exit file => interrupted (reboot semantics)."""
    store, manager = job_env
    from uaeg import paths

    async def scenario():
        job = manager.spawn("sleep 30")
        job_id = job["job_id"]
        pid = job["pid"]
        await asyncio.sleep(0.4)
        # simulate reboot: server stops watching first...
        await manager.shutdown()
        # ...then the process group is SIGKILLed with no exit file
        os.killpg(pid, 9)
        await asyncio.sleep(0.5)
        manager2 = JobManager(store)
        counts = manager2.reattach()
        assert counts["interrupted"] == 1
        assert store.get_job(job_id)["status"] == "interrupted"
        await manager2.shutdown()

    asyncio.run(scenario())


# ----------------------------------------------------------------------
# PTY sessions
# ----------------------------------------------------------------------
@pytest.fixture()
def session_env(env):
    from uaeg import paths

    store = Store(paths.database_path())
    manager = SessionManager(store)
    yield store, manager
    try:
        asyncio.run(manager.shutdown())
    except RuntimeError:
        pass
    store.close()


async def _wait_output(manager: SessionManager, session_id: str, predicate, timeout=10.0):
    """Poll shell_output like an agent would until predicate matches."""
    deadline = time.time() + timeout
    seq = 0
    last = ""
    while time.time() < deadline:
        out = manager.output(session_id, since_seq=seq)
        last += out["data"]
        seq = out["seq"]
        if predicate(last):
            return last
        await asyncio.sleep(0.15)
    raise AssertionError(f"timeout waiting for output; last={last[-300:]!r}")


def test_pty_interactive_shell(session_env):
    store, manager = session_env

    async def scenario():
        info = manager.open()
        sid = info["session_id"]
        assert manager.list()[0]["session_id"] == sid

        manager.input(sid, "echo PTY_OK_7f3", add_newline=True)
        data = await _wait_output(manager, sid, lambda d: "PTY_OK_7f3" in d)
        assert "PTY_OK_7f3" in data

        manager.input(sid, "exit", add_newline=True)
        # v0.3.4 contract: the session STAYS readable for a grace window
        # after the shell exits - the agent can collect the exit status.
        # (v0.3.3 dropped the session the instant the process died, losing
        # the final output chunk and the exit code.)
        for _ in range(120):
            await asyncio.sleep(0.1)
            out = manager.output(sid, since_seq=0)
            if out["alive"] is False and out["exit_code"] is not None:
                break
        assert out["alive"] is False, "session should be closed after the shell exits"
        assert out["exit_code"] == 0, "exit code of a clean 'exit' must be reported"
        assert store.get_session(sid)["status"] == "closed"

    asyncio.run(scenario())


def test_pty_ctrl_c(session_env):
    store, manager = session_env

    async def scenario():
        info = manager.open()
        sid = info["session_id"]
        manager.input(sid, "sleep 60", add_newline=True)
        await asyncio.sleep(0.8)
        manager.input(sid, "\x03")  # Ctrl-C
        await _wait_output(
            manager, sid, lambda d: "Interrupt" in d or "^C" in d, timeout=8
        )
        await manager.close(sid)

    asyncio.run(scenario())


def test_pty_resize(session_env):
    store, manager = session_env

    async def scenario():
        info = manager.open(command="bash", cols=100, rows=30)
        sid = info["session_id"]
        manager.resize(sid, 200, 50)
        manager.input(sid, "stty size", add_newline=True)
        await _wait_output(manager, sid, lambda d: "50 200" in d, timeout=8)
        await manager.close(sid)

    asyncio.run(scenario())


def test_pty_ring_buffer_bounds(session_env):
    store, manager = session_env

    async def scenario():
        manager.buffer_bytes = 4096
        info = manager.open(command="bash")
        sid = info["session_id"]
        manager.input(
            sid,
            "for i in $(seq 1 400); do echo line-$i-0123456789; done; echo END_MARK",
            add_newline=True,
        )
        await _wait_output(manager, sid, lambda d: "END_MARK" in d, timeout=20)
        session = manager.get(sid)
        assert len(session._buffer) <= 4096
        info = session.info()
        assert info["seq_total"] > 4096  # counted more than retained
        out = manager.output(sid, since_seq=0)
        assert out["truncated"] is True
        assert out["seq"] == info["seq_total"]
        await manager.close(sid)

    asyncio.run(scenario())


def test_pty_unknown_session_error(session_env):
    store, manager = session_env
    with pytest.raises(Exception):
        manager.output("nonexistent")


def test_pty_startup_reconcile(session_env):
    store, manager = session_env

    async def scenario():
        manager.open()
        manager2 = SessionManager(store)
        count = manager2.startup_reconcile()
        assert count == 1
        assert store.open_sessions() == []

    asyncio.run(scenario())
