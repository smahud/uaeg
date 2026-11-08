"""Regression tests: revocation containment and standalone CLI actions.

Covers the P2 finding "incomplete process termination after key
revocation": revoking a key must terminate that key's live PTY sessions and
running background jobs, while other keys are unaffected.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from uaeg import security
from uaeg.containment import (
    cancel_job_standalone,
    kill_session_standalone,
    terminate_key_processes,
)
from uaeg.db import Store
from uaeg.executor.jobs import JobManager
from uaeg.executor.pty import SessionManager


@pytest.fixture()
def managers(env):
    store = Store(env["data"] / "uaeg.db")
    jobs = JobManager(store)
    sessions = SessionManager(store)
    yield store, jobs, sessions
    asyncio.run(jobs.shutdown())
    try:
        asyncio.run(sessions.shutdown())
    except RuntimeError:
        pass
    store.close()


def _make_key(store: Store, name: str) -> tuple[str, str]:
    material = security.new_key(name)
    store.create_key(
        key_id=material.key_id, name=material.name, prefix=material.prefix,
        verifier_hex=material.verifier,
    )
    return material.key_id, material.secret


async def _wait_output(manager: SessionManager, sid: str, predicate, timeout=10.0):
    deadline = time.time() + timeout
    seq = 0
    last = ""
    while time.time() < deadline:
        out = manager.output(sid, since_seq=seq)
        last += out["data"]
        seq = out["seq"]
        if predicate(last):
            return last
        await asyncio.sleep(0.15)
    raise AssertionError(f"timeout waiting for output; last={last[-200:]!r}")


def test_terminate_key_processes_kills_sessions_and_jobs(managers):
    store, jobs, sessions = managers
    key_a, _ = _make_key(store, "A")
    key_b, _ = _make_key(store, "B")

    async def scenario():
        # key A: one live PTY session + one long background job
        info_a = sessions.open(command="bash", key_id=key_a, key_name="A")
        job_a = jobs.spawn("sleep 300", key_id=key_a, key_name="A")
        # key B: one live PTY session + one long background job
        info_b = sessions.open(command="bash", key_id=key_b, key_name="B")
        job_b = jobs.spawn("sleep 300", key_id=key_b, key_name="B")
        await asyncio.sleep(0.5)

        result = await asyncio.to_thread(
            terminate_key_processes, store, key_a
        )
        assert result["terminated_sessions"] == 1
        assert result["terminated_jobs"] == 1
        await asyncio.sleep(0.5)

        # key A's session is terminated: closed in the registry (its tail
        # stays readable during the grace window - v0.3.4) and closed in the
        # database; the shell process itself is dead.
        sess_a = sessions.get(info_a["session_id"])
        assert sess_a.closed is True
        assert store.get_session(info_a["session_id"])["status"] == "closed"
        # key A's job is cancelled
        assert store.get_job(job_a["job_id"])["status"] == "cancelled"
        # key B is untouched and still LIVE (not merely readable)
        sess_b = sessions.get(info_b["session_id"])
        assert sess_b.closed is False
        assert store.get_job(job_b["job_id"])["status"] == "running"

        # repeat invocation is a no-op (idempotent containment)
        result2 = await asyncio.to_thread(terminate_key_processes, store, key_a)
        assert result2["terminated_sessions"] == 0
        assert result2["terminated_jobs"] == 0

        # cleanup key B
        await sessions.close(info_b["session_id"])
        await jobs.cancel(job_b["job_id"])

    asyncio.run(scenario())


def test_cancel_job_standalone(managers):
    store, jobs, _ = managers
    key, _ = _make_key(store, "t")

    async def scenario():
        job = jobs.spawn("sleep 60", key_id=key, key_name="t")
        await asyncio.sleep(0.4)
        final = await asyncio.to_thread(cancel_job_standalone, store, job["job_id"])
        assert final["status"] == "cancelled"
        # already final -> idempotent
        final2 = await asyncio.to_thread(cancel_job_standalone, store, job["job_id"])
        assert final2["status"] == "cancelled"

    asyncio.run(scenario())
    assert cancel_job_standalone(store, "job-nonexistent") is None


def test_kill_session_standalone(managers):
    store, _, sessions = managers
    key, _ = _make_key(store, "t")

    async def scenario():
        info = sessions.open(command="bash", key_id=key, key_name="t")
        sid = info["session_id"]
        await _wait_output(sessions, sid, lambda d: b_prompt_seen(d))
        final = await asyncio.to_thread(kill_session_standalone, store, sid)
        assert final["status"] == "closed"
        await asyncio.sleep(0.6)
        # v0.3.4: the terminated session stays readable during the grace
        # window (closed, exit code available); it no longer VANISHES.
        out = sessions.output(sid, since_seq=0)
        assert out["alive"] is False
        assert sessions.get(sid).closed is True

    def b_prompt_seen(data: str) -> bool:
        return len(data) > 0  # any prompt output means the shell is alive

    asyncio.run(scenario())
    assert kill_session_standalone(store, "nonexistent") is None


def test_key_resources_listing(managers):
    from uaeg.containment import key_resources

    store, jobs, _ = managers
    key, _ = _make_key(store, "t")

    async def scenario():
        jobs.spawn("sleep 60", key_id=key, key_name="t")
        await asyncio.sleep(0.3)
        res = key_resources(store, key)
        assert len(res["jobs"]) == 1
        assert res["sessions"] == []
        await jobs.cancel(res["jobs"][0]["job_id"])

    asyncio.run(scenario())
