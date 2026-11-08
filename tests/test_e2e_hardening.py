"""End-to-end regression tests for the production-hardening release.

Covers:
- Revocation containment via the dashboard (P2 suspect issue #5).
- Audit-log secret redaction through the full MCP path (P2 issue #4).
- Concurrent clients and key operations.
- High-volume background job output with pagination.
- Fail-closed startup: invalid CIDR config, corrupt TLS cert, listener bind
  conflict (exit codes, clean messages).
- CLI jobs cancel / sessions kill against a live server.
- Version metadata consistency (docs/package/installer).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from conftest import Server, make_key, write_test_config
from test_e2e_smoke import MCP_HEADERS_BASE, McpClient, server  # noqa: F401  (fixture)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn_serve(env) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "uaeg", "serve"],
        env={**os.environ},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


async def _wait_dead(proc: subprocess.Popen, timeout: float = 25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return proc.returncode, (proc.stdout.read() or "") if proc.stdout else ""
        await asyncio.sleep(0.2)
    proc.kill()
    return proc.returncode, "STILL RUNNING AFTER TIMEOUT"


# ----------------------------------------------------------------------
# Revocation containment (suspect issue #5)
# ----------------------------------------------------------------------
async def test_revocation_terminates_key_processes(server, env):
    _, secret_a = make_key(env, "ContainA")
    _, secret_b = make_key(env, "ContainB")
    ca = McpClient(server.mcp_port, secret_a)
    cb = McpClient(server.mcp_port, secret_b)
    await ca.initialize()
    await cb.initialize()

    # key A opens a live PTY session and starts a long background job
    opened = await ca.call_tool("shell_open", {"command": "bash"})
    sid_a = opened["session_id"]
    job = await ca.call_tool(
        "shell_exec", {"command": "sleep 300", "background": True}
    )
    job_id_a = job["job_id"]
    await asyncio.sleep(0.4)

    # key B also has live work that must survive A's revocation
    opened_b = await cb.call_tool("shell_open", {"command": "bash"})
    sid_b = opened_b["session_id"]
    job_b = await cb.call_tool(
        "shell_exec", {"command": "sleep 300", "background": True}
    )

    # revoke key A through the dashboard API (containment by default)
    base = f"http://127.0.0.1:{server.dashboard_port}"
    async with httpx.AsyncClient(timeout=30) as dash:
        from uaeg import paths as uaeg_paths
        from uaeg.db import Store

        store = Store(uaeg_paths.database_path())
        key_a = next(k for k in store.list_keys() if k["name"] == "ContainA")
        store.close()
        r = await dash.post(f"{base}/api/keys/{key_a['key_id']}/revoke")
        assert r.status_code == 200
        containment = r.json()["containment"]
        assert containment["terminated_sessions"] == 1
        assert containment["terminated_jobs"] == 1

    await asyncio.sleep(0.5)

    # v0.3.4 key scoping: key B can no longer inspect key A's resources -
    # cross-key access is rejected with a clear error (it used to be
    # allowed in the trusted-operator MVP model and enabled cross-key PTY
    # input injection).
    out = await cb.call_tool("shell_output", {"session_id": sid_a, "since_seq": 0})
    assert out.get("ok") is False
    assert "different agent key" in out.get("error", "")
    st = await cb.call_tool("job_status", {"job_id": job_id_a})
    assert st.get("ok") is False
    assert "different agent key" in st.get("error", "")

    # the OPERATOR (dashboard, passwordless legacy mode on the private test
    # bind) still sees everything: A's session closed and its job cancelled
    base = f"http://127.0.0.1:{server.dashboard_port}"
    async with httpx.AsyncClient(timeout=30) as dash:
        r = await dash.get(f"{base}/api/jobs")
        jobs = {j["job_id"]: j for j in r.json()["jobs"]}
        assert jobs[job_id_a]["status"] == "cancelled"
        assert jobs[job_b["job_id"]]["status"] == "running"
        r = await dash.get(f"{base}/api/sessions")
        hist = {s["session_id"]: s for s in r.json()["history"]}
        assert hist[sid_a]["status"] == "closed"

    # key A can no longer authenticate at all
    async with httpx.AsyncClient(
        verify=__import__("ssl")._create_unverified_context(), timeout=10
    ) as raw:
        r = await raw.post(
            f"https://127.0.0.1:{server.mcp_port}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**MCP_HEADERS_BASE, "Authorization": f"Bearer {secret_a}"},
        )
        assert r.status_code == 401

    # key B is unaffected: session alive, job running (its own resources)
    sessions = await cb.call_tool("shell_sessions", {})
    assert any(s["session_id"] == sid_b for s in sessions["sessions"])
    assert all(s["session_id"] != sid_a for s in sessions["sessions"])
    st_b = await cb.call_tool("job_status", {"job_id": job_b["job_id"]})
    assert st_b["status"] == "running"

    # cleanup key B's work via key B's own client
    await cb.call_tool("shell_close", {"session_id": sid_b})
    await cb.call_tool("job_cancel", {"job_id": job_b["job_id"]})
    await ca.aclose()
    await cb.aclose()


async def test_revocation_keep_processes_opt_out(server, env):
    _, secret = make_key(env, "KeepProc")
    _, secret_b = make_key(env, "KeepProcB")
    c = McpClient(server.mcp_port, secret)
    cb = McpClient(server.mcp_port, secret_b)
    await c.initialize()
    await cb.initialize()
    job = await c.call_tool(
        "shell_exec", {"command": "sleep 120", "background": True}
    )
    await asyncio.sleep(0.3)

    base = f"http://127.0.0.1:{server.dashboard_port}"
    async with httpx.AsyncClient(timeout=30) as dash:
        from uaeg import paths as uaeg_paths
        from uaeg.db import Store

        store = Store(uaeg_paths.database_path())
        key = next(k for k in store.list_keys() if k["name"] == "KeepProc")
        store.close()
        r = await dash.post(
            f"{base}/api/keys/{key['key_id']}/revoke", json={"keep_processes": True}
        )
        assert r.status_code == 200
        assert r.json()["containment"] == {
            "terminated_sessions": 0, "terminated_jobs": 0,
        }

    # v0.3.4 key scoping: key B cannot see key A's job anymore; verify via
    # the operator dashboard instead, then clean up through it as well.
    st = await cb.call_tool("job_status", {"job_id": job["job_id"]})
    assert st.get("ok") is False
    assert "different agent key" in st.get("error", "")
    base = f"http://127.0.0.1:{server.dashboard_port}"
    async with httpx.AsyncClient(timeout=30) as dash:
        r = await dash.get(f"{base}/api/jobs")
        jobs = {j["job_id"]: j for j in r.json()["jobs"]}
        assert jobs[job["job_id"]]["status"] == "running"  # still running by choice
        r = await dash.post(f"{base}/api/jobs/{job['job_id']}/cancel")
        assert r.status_code == 200
    await c.aclose()
    await cb.aclose()


# ----------------------------------------------------------------------
# Audit-log redaction through the full MCP path (suspect issue #4)
# ----------------------------------------------------------------------
async def test_audit_log_redaction_e2e(server, env):
    _, secret = make_key(env, "RedactAgent")
    c = McpClient(server.mcp_port, secret)
    await c.initialize()

    marker = "REDACT-MARKER-7q"
    secret_token = "topsecret-token-987654321"
    res = await c.call_tool(
        "shell_exec",
        {
            "command": (
                f'echo "{marker}" && curl -s -H "Authorization: Bearer {secret_token}" '
                "https://127.0.0.1:1/ >/dev/null; true"
            )
        },
    )
    assert res["exit_code"] == 0
    # the caller still receives the raw stdout of its own command
    assert marker in res["stdout"]

    from uaeg import paths as uaeg_paths
    from uaeg.db import Store

    store = Store(uaeg_paths.database_path())
    logs = [
        l for l in store.list_logs(limit=20)
        if l["tool"] == "shell_exec" and marker in (l["meta"].get("command") or "")
    ]
    store.close()
    assert logs, "audit entry for the redaction test command not found"
    entry = logs[0]
    # the secret is redacted, the command shape is preserved
    assert secret_token not in entry["meta"]["command"]
    assert "Authorization: Bearer ***REDACTED***" in entry["meta"]["command"]
    assert marker in entry["meta"]["command"]
    assert secret_token not in (entry.get("output_preview") or "")

    # the dashboard Logs API shows the redacted form
    base = f"http://127.0.0.1:{server.dashboard_port}"
    async with httpx.AsyncClient(timeout=10) as dash:
        r = await dash.get(f"{base}/api/logs?limit=50")
        assert r.status_code == 200
        body = json.dumps(r.json())
        assert secret_token not in body
        assert "***REDACTED***" in body
    await c.aclose()


# ----------------------------------------------------------------------
# Concurrency (mission testing requirements)
# ----------------------------------------------------------------------
async def test_concurrent_clients_and_key_ops(server, env):
    n = 6
    clients = []
    for i in range(n):
        _, secret = make_key(env, f"Conc{i}")
        c = McpClient(server.mcp_port, secret)
        await c.initialize()
        clients.append(c)

    base = f"http://127.0.0.1:{server.dashboard_port}"

    async def _work(i: int) -> None:
        res = await clients[i].call_tool("shell_exec", {"command": f"echo conc-{i}"})
        assert res["exit_code"] == 0
        assert f"conc-{i}" in res["stdout"]

    async def _dashboard_key_ops() -> None:
        async with httpx.AsyncClient(timeout=30) as dash:
            r = await dash.post(
                f"{base}/api/keys", json={"name": "conc-dash", "permission_mode": "all"}
            )
            assert r.status_code == 201
            kid = r.json()["key"]["key_id"]
            r = await dash.get(f"{base}/api/keys")
            assert any(k["key_id"] == kid for k in r.json()["keys"])

    results = await asyncio.gather(
        *[_work(i) for i in range(n)], _dashboard_key_ops(),
        return_exceptions=True,
    )
    assert not [r for r in results if isinstance(r, Exception)]

    # repeated connect/disconnect on one key
    _, secret = make_key(env, "Reconnect")
    for _ in range(3):
        c = McpClient(server.mcp_port, secret)
        await c.initialize()
        res = await c.call_tool("shell_exec", {"command": "echo re"})
        assert res["exit_code"] == 0
        await c.aclose()

    for c in clients:
        await c.aclose()


# ----------------------------------------------------------------------
# High-volume output and pagination
# ----------------------------------------------------------------------
async def test_high_volume_background_output(server, env):
    _, secret = make_key(env, "Volume")
    c = McpClient(server.mcp_port, secret)
    await c.initialize()

    res = await c.call_tool(
        "shell_exec",
        {"command": "seq 1 300000", "background": True},  # ~ 2 MB
    )
    job_id = res["job_id"]
    final = await c.call_tool(
        "job_wait", {"job_id": job_id, "timeout_seconds": 120}
    )
    assert final["status"] == "completed"

    collected = []
    offset = 0
    while True:
        out = await c.call_tool(
            "job_output",
            {"job_id": job_id, "stream": "stdout", "offset": offset, "limit": 65536},
        )
        collected.append(out["data"])
        offset = out["offset"]
        if offset >= out["available"]:
            break
    data = "".join(collected)
    assert data.startswith("1\n2\n")
    assert data.rstrip("\n").endswith("300000")
    assert out["available"] > 1_500_000  # genuinely high volume

    # sync path: bounded capture flags (default cap is 1 MiB per stream)
    res = await c.call_tool(
        "shell_exec",
        {"command": "seq 1 300000", "timeout_seconds": 120,
         "cwd": "/tmp"},
    )
    assert res["exit_code"] == 0
    assert res["stdout_truncated"] is True
    assert res["stdout_bytes"] > 1_500_000
    assert len(res["stdout"]) < 1_100_000
    await c.aclose()


# ----------------------------------------------------------------------
# Fail-closed startup behaviors (subprocess level)
# ----------------------------------------------------------------------
async def test_server_exits_nonzero_on_listener_bind_conflict(env):
    write_test_config(env)
    cfg = json.loads(env["config"].read_text())
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", cfg["mcp"]["port"]))
    blocker.listen(1)
    try:
        proc = _spawn_serve(env)
        rc, out = await _wait_dead(proc, timeout=25)
        assert rc not in (0, None), f"service stayed alive: {out[-400:]}"
        assert "listener failed" in out or "failed to start" in out
    finally:
        blocker.close()


async def test_server_refuses_invalid_cidr_config(env):
    write_test_config(env)
    cfg = json.loads(env["config"].read_text())
    cfg["network"] = {"allowed_dashboard_cidrs": ["192.168.1.0/not-a-cidr"]}
    env["config"].write_text(json.dumps(cfg))
    proc = _spawn_serve(env)
    rc, out = await _wait_dead(proc, timeout=25)
    assert rc == 2
    assert "invalid CIDR" in out
    assert "Traceback" not in out


async def test_server_clean_error_on_invalid_tls_cert(env):
    write_test_config(env)
    cfg = json.loads(env["config"].read_text())
    bad_cert = env["data"] / "bad.crt"
    bad_key = env["data"] / "bad.key"
    bad_cert.write_text("this is not a certificate")
    bad_key.write_text("this is not a key")
    cfg["tls"] = {
        "mode": "manual",
        "cert": str(bad_cert),
        "key": str(bad_key),
    }
    env["config"].write_text(json.dumps(cfg))
    proc = _spawn_serve(env)
    rc, out = await _wait_dead(proc, timeout=25)
    assert rc == 2
    assert "cannot load the TLS certificate" in out
    assert "Traceback" not in out


# ----------------------------------------------------------------------
# CLI jobs cancel / sessions kill against a live server
# ----------------------------------------------------------------------
async def test_cli_jobs_cancel_and_sessions_kill(server, env):
    _, secret = make_key(env, "CliOps")
    c = McpClient(server.mcp_port, secret)
    await c.initialize()

    job = await c.call_tool(
        "shell_exec", {"command": "sleep 300", "background": True}
    )
    opened = await c.call_tool("shell_open", {"command": "bash"})
    sid = opened["session_id"]
    await asyncio.sleep(0.5)

    def _cli(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "uaeg", *args],
            capture_output=True, text=True, timeout=120,
            env={**os.environ},
        )

    # uaeg jobs cancel <id>
    res = _cli("jobs", "cancel", job["job_id"])
    assert res.returncode == 0, res.stdout + res.stderr
    st = await c.call_tool("job_status", {"job_id": job["job_id"]})
    assert st["status"] == "cancelled"

    # uaeg sessions kill <id>
    res = _cli("sessions", "kill", sid)
    assert res.returncode == 0, res.stdout + res.stderr
    await asyncio.sleep(0.6)
    # v0.3.4: the killed session stays listed (alive=false) and readable
    # during the grace window - with its exit code - instead of vanishing.
    sessions = await c.call_tool("shell_sessions", {})
    entry = next((s for s in sessions["sessions"] if s["session_id"] == sid), None)
    assert entry is not None and entry["alive"] is False
    out = await c.call_tool("shell_output", {"session_id": sid, "since_seq": 0})
    assert out["alive"] is False
    assert isinstance(out["exit_code"], int)
    await c.aclose()


# ----------------------------------------------------------------------
# Version metadata consistency (suspect issue #7)
# ----------------------------------------------------------------------
def test_version_metadata_consistency():
    import tomllib

    import uaeg

    repo = Path(__file__).resolve().parent.parent
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == uaeg.__version__

    installer = (repo / "install.sh").read_text()
    assert f'UAEG_RELEASE_VERSION="{uaeg.__version__}"' in installer

    readme = (repo / "README.md").read_text()
    assert f"v{uaeg.__version__}" in readme  # no stale version references

    # the packaged version is reported by the CLI entry point
    res = subprocess.run(
        [sys.executable, "-m", "uaeg", "version"],
        capture_output=True, text=True, timeout=30,
    )
    assert res.stdout.strip() == f"uaeg {uaeg.__version__}"


# ----------------------------------------------------------------------
# Key rotation (dashboard + CLI)
# ----------------------------------------------------------------------
async def test_dashboard_key_rotation(server, env):
    base = f"http://127.0.0.1:{server.dashboard_port}"
    async with httpx.AsyncClient(timeout=30) as dash:
        r = await dash.post(
            f"{base}/api/keys", json={"name": "RotateMe", "permission_mode": "all"}
        )
        assert r.status_code == 201
        old_secret = r.json()["secret"]
        kid = r.json()["key"]["key_id"]

        # the old secret works before rotation
        c = McpClient(server.mcp_port, old_secret)
        await c.initialize()
        res = await c.call_tool("shell_exec", {"command": "echo pre-rotate"})
        assert res["exit_code"] == 0
        await c.aclose()

        r = await dash.post(f"{base}/api/keys/{kid}/rotate")
        assert r.status_code == 200
        body = r.json()
        new_secret = body["secret"]
        new_kid = body["key"]["key_id"]
        assert new_kid != kid
        # rotation is continuity, not containment: no processes terminated
        assert "containment" not in body

        r = await dash.get(f"{base}/api/keys")
        statuses = {k["key_id"]: k["status"] for k in r.json()["keys"]}
        assert statuses[kid] == "revoked"
        assert statuses[new_kid] == "active"

    # old secret is rejected, new secret works
    async with httpx.AsyncClient(
        verify=__import__("ssl")._create_unverified_context(), timeout=10
    ) as raw:
        r = await raw.post(
            f"https://127.0.0.1:{server.mcp_port}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**MCP_HEADERS_BASE, "Authorization": f"Bearer {old_secret}"},
        )
        assert r.status_code == 401

    c2 = McpClient(server.mcp_port, new_secret)
    await c2.initialize()
    res = await c2.call_tool("shell_exec", {"command": "echo post-rotate"})
    assert res["exit_code"] == 0
    await c2.aclose()


async def test_cli_key_rotation_and_revoke_containment(server, env):
    def _cli(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "uaeg", *args],
            capture_output=True, text=True, timeout=120,
            env={**os.environ},
        )

    r = _cli("generate", "--name", "CliRotate")
    secret = next(
        (l.strip() for l in r.stdout.splitlines() if l.strip().startswith("uaeg_")), "")
    kid = next(
        (l.split()[0] for l in _cli("keys", "list").stdout.splitlines()
         if "CliRotate" in l), "")
    assert secret and kid

    r = _cli("keys", "rotate", kid)
    assert r.returncode == 0, r.stdout + r.stderr
    import re

    m = re.search(r"uaeg_[A-Za-z0-9_-]{43}", r.stdout)
    new_secret = m.group(0) if m else ""
    assert new_secret, r.stdout

    c = McpClient(server.mcp_port, new_secret)
    await c.initialize()
    # CLI revocation with containment on the rotated key
    new_kid = next(
        (l.split()[0] for l in _cli("keys", "list").stdout.splitlines()
         if "CliRotate" in l and "active" in l), "")
    job = await c.call_tool("shell_exec", {"command": "sleep 200", "background": True})
    await asyncio.sleep(0.3)
    r = _cli("keys", "revoke", new_kid)
    assert r.returncode == 0
    assert "terminated 0 session" in r.stdout  # no live sessions for this key
    await asyncio.sleep(0.4)
    from uaeg import paths as uaeg_paths
    from uaeg.db import Store

    store = Store(uaeg_paths.database_path())
    assert store.get_job(job["job_id"])["status"] == "cancelled"
    store.close()
    await c.aclose()
