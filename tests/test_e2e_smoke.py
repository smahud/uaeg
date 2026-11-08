"""End-to-end smoke test: real server, real TLS, real MCP protocol.

Covers the PRD flows that can be validated without a VPS:
- AT-02 custom ports, AT-03 key generation, AT-04 MCP over HTTPS + bearer,
  AT-05 execution context, AT-07 file operations, AT-10 PTY I/O,
  AT-11 background job lifecycle, AT-12 revocation, AT-13 second key,
  AT-16 restart persistence, AT-17 audit logs, AT-18 (no SSH involved),
  plus dashboard API (AT-14 proxy) and healthz (FR-020).
"""

from __future__ import annotations

import asyncio
import json
import ssl
import time

import httpx
import pytest

from conftest import Server, make_key, write_test_config

MCP_HEADERS_BASE = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


class McpClient:
    """Minimal MCP Streamable HTTP client for testing."""

    def __init__(self, port: int, secret: str):
        ctx = ssl._create_unverified_context()
        self.http = httpx.AsyncClient(verify=ctx, timeout=30.0)
        self.url = f"https://127.0.0.1:{port}/mcp"
        self.headers = {**MCP_HEADERS_BASE, "Authorization": f"Bearer {secret}"}
        self.session_id: str | None = None
        self._next_id = 0

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def post(self, payload: dict, expect_status: int | None = 200):
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        resp = await self.http.post(self.url, json=payload, headers=headers)
        if expect_status is not None:
            assert resp.status_code == expect_status, (
                f"{payload.get('method')}: HTTP {resp.status_code}: {resp.text[:500]}"
            )
        if resp.status_code == 200 and "mcp-session-id" in resp.headers:
            self.session_id = resp.headers["mcp-session-id"]
        return resp

    async def initialize(self) -> dict:
        resp = await self.post(
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "uaeg-smoke", "version": "0.0.1"},
                },
            }
        )
        result = resp.json()["result"]
        # acknowledge
        await self.post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_status=202,
        )
        return result

    async def call_tool(self, name: str, arguments: dict) -> dict:
        resp = await self.post(
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        body = resp.json()
        assert "error" not in body, f"tools/call {name}: {body['error']}"
        result = body["result"]
        assert not result.get("isError", False), f"tool {name} errored: {result}"
        # structured content when available, else parse text
        if result.get("structuredContent"):
            return result["structuredContent"]
        text = result["content"][0]["text"]
        return json.loads(text)

    async def list_tools(self) -> list[str]:
        resp = await self.post(
            {"jsonrpc": "2.0", "id": self._id(), "method": "tools/list"}
        )
        return [t["name"] for t in resp.json()["result"]["tools"]]

    async def aclose(self):
        await self.http.aclose()


@pytest.fixture()
def server(env):
    cfg = write_test_config(env)
    mcp_port = json.loads(cfg.read_text())["mcp"]["port"]
    dash_port = json.loads(cfg.read_text())["dashboard"]["port"]
    srv = Server(env, mcp_port, dash_port)
    srv.start()
    yield srv
    srv.stop()


# ----------------------------------------------------------------------
def test_healthz_and_root(server, env):
    ctx = ssl._create_unverified_context()
    with httpx.Client(verify=ctx, timeout=10) as client:
        r = client.get(f"https://127.0.0.1:{server.mcp_port}/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["service"] == "UAEG"

        r = client.get(f"https://127.0.0.1:{server.mcp_port}/")
        assert r.status_code == 200
        assert "mcp_endpoint" in r.json()


async def test_mcp_requires_auth(server, env):
    ctx = ssl._create_unverified_context()
    async with httpx.AsyncClient(verify=ctx, timeout=10) as client:
        r = await client.post(
            f"https://127.0.0.1:{server.mcp_port}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers=MCP_HEADERS_BASE,
        )
        assert r.status_code == 401
        assert "www-authenticate" in {k.lower() for k in r.headers}

        r = await client.post(
            f"https://127.0.0.1:{server.mcp_port}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={**MCP_HEADERS_BASE, "Authorization": "Bearer uaeg_invalid"},
        )
        assert r.status_code == 401


async def test_full_mcp_flow(server, env):
    key_id, secret = make_key(env, "Claude")

    client = McpClient(server.mcp_port, secret)
    try:
        # AT-04: initialize over HTTPS with bearer auth
        init = await client.initialize()
        assert init["serverInfo"]["name"] == "UAEG"
        assert init["serverInfo"]["version"] == __import__("uaeg").__version__
        assert init["protocolVersion"]

        # tool discovery
        tools = await client.list_tools()
        for expected in [
            "shell_exec", "shell_open", "shell_input", "shell_output",
            "shell_resize", "shell_close", "shell_sessions",
            "job_status", "job_output", "job_wait", "job_cancel", "job_list",
            "server_info",
        ]:
            assert expected in tools, f"missing tool: {expected}"

        # server_info reports the key identity
        info = await client.call_tool("server_info", {})
        assert info["key"]["key_id"] == key_id
        assert info["key"]["name"] == "Claude"
        assert info["exec_user"]

        # AT-05: whoami reflects the real execution user
        res = await client.call_tool("shell_exec", {"command": "id -un"})
        assert res["exit_code"] == 0
        assert res["stdout"].strip() == info["exec_user"]

        # env + cwd + exit code + stderr
        res = await client.call_tool(
            "shell_exec",
            {"command": "echo $GREETING; pwd; echo bad >&2; exit 5",
             "env": {"GREETING": "hi-from-agent"}, "cwd": "/tmp"},
        )
        assert res["exit_code"] == 5
        assert "hi-from-agent" in res["stdout"]
        assert "/tmp" in res["stdout"]
        assert "bad" in res["stderr"]

        # timeout path
        res = await client.call_tool("shell_exec", {"command": "sleep 30", "timeout_seconds": 2})
        assert res["timed_out"] is True

        # AT-07: create/modify/delete files
        res = await client.call_tool(
            "shell_exec",
            {"command": "echo agent-was-here > /tmp/uaeg_at07.txt && cat /tmp/uaeg_at07.txt && rm /tmp/uaeg_at07.txt"},
        )
        assert res["exit_code"] == 0
        assert "agent-was-here" in res["stdout"]
    finally:
        await client.aclose()


async def test_background_jobs_and_reconnect(server, env):
    _, secret = make_key(env, "JobAgent")
    client = McpClient(server.mcp_port, secret)
    try:
        await client.initialize()

        # start a background job
        res = await client.call_tool(
            "shell_exec",
            {"command": "sleep 2 && echo finished-well", "background": True},
        )
        assert res["background"] is True
        job_id = res["job_id"]
        assert res["status"] == "running"

        # AT-11: "disconnect" (new client/session) and re-check the job
        client2 = McpClient(server.mcp_port, secret)
        await client2.initialize()
        st = await client2.call_tool("job_status", {"job_id": job_id})
        assert st["status"] in ("running", "completed", "failed")
        final = await client2.call_tool("job_wait", {"job_id": job_id, "timeout_seconds": 15})
        assert final["status"] == "completed"
        assert final["exit_code"] == 0
        await client2.aclose()

        # output retrieval with pagination
        out = await client.call_tool(
            "job_output", {"job_id": job_id, "stream": "stdout", "offset": 0, "limit": 5}
        )
        assert out["data"] == "finis"
        assert out["available"] == 14
        out2 = await client.call_tool(
            "job_output", {"job_id": job_id, "stream": "stdout", "offset": out["offset"]}
        )
        assert out2["data"] == "hed-well\n"

        # cancel a long-running job
        res = await client.call_tool(
            "shell_exec", {"command": "sleep 300", "background": True}
        )
        cancelled = await client.call_tool("job_cancel", {"job_id": res["job_id"]})
        assert cancelled["status"] == "cancelled"

        # job listing
        listing = await client.call_tool("job_list", {"limit": 10})
        assert any(j["job_id"] == job_id for j in listing["jobs"])
    finally:
        await client.aclose()


async def test_pty_session_flow(server, env):
    _, secret = make_key(env, "PtyAgent")
    client = McpClient(server.mcp_port, secret)
    try:
        await client.initialize()

        # AT-10: open, exchange input/output, resize, close
        opened = await client.call_tool("shell_open", {"cols": 120, "rows": 30})
        sid = opened["session_id"]

        await client.call_tool(
            "shell_input", {"session_id": sid, "data": "echo PTY_E2E_OK", "add_newline": True}
        )
        data = ""
        seq = 0
        deadline = time.time() + 10
        while time.time() < deadline:
            out = await client.call_tool(
                "shell_output", {"session_id": sid, "since_seq": seq}
            )
            data += out["data"]
            seq = out["seq"]
            if "PTY_E2E_OK" in data:
                break
            await asyncio.sleep(0.15)
        assert "PTY_E2E_OK" in data

        # resize reflects via stty
        await client.call_tool("shell_resize", {"session_id": sid, "cols": 200, "rows": 50})
        await client.call_tool(
            "shell_input", {"session_id": sid, "data": "stty size", "add_newline": True}
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            out = await client.call_tool(
                "shell_output", {"session_id": sid, "since_seq": seq}
            )
            data += out["data"]
            seq = out["seq"]
            if "50 200" in data:
                break
            await asyncio.sleep(0.15)
        assert "50 200" in data

        # session listing (reconnection support)
        sessions = await client.call_tool("shell_sessions", {})
        assert any(s["session_id"] == sid for s in sessions["sessions"])

        closed = await client.call_tool("shell_close", {"session_id": sid})
        assert closed["ok"] is True

        # v0.3.4: the closed session stays readable for a grace window -
        # final output + exit code remain available (alive=false) instead of
        # the session vanishing the instant it is closed.
        out = await client.call_tool("shell_output", {"session_id": sid, "since_seq": 0})
        assert out["alive"] is False
        assert isinstance(out["exit_code"], int)
    finally:
        await client.aclose()


async def test_key_revocation_and_second_key(server, env):
    # AT-13: two independent keys
    kid_a, secret_a = make_key(env, "AgentA")
    kid_b, secret_b = make_key(env, "AgentB")

    ca = McpClient(server.mcp_port, secret_a)
    cb = McpClient(server.mcp_port, secret_b)
    try:
        await ca.initialize()
        await cb.initialize()
        ra = await ca.call_tool("shell_exec", {"command": "echo A"})
        rb = await cb.call_tool("shell_exec", {"command": "echo B"})
        assert "A" in ra["stdout"], f"agent A response: {ra}"
        assert "B" in rb["stdout"], f"agent B response: {rb}"
        info_a = await ca.call_tool("server_info", {})
        assert info_a["key"]["key_id"] == kid_a
    finally:
        await ca.aclose()
        await cb.aclose()

    # AT-12: revoke key A -> immediate rejection, key B unaffected
    from uaeg import paths as uaeg_paths
    from uaeg.db import Store

    store = Store(uaeg_paths.database_path())
    store.revoke_key(kid_a)
    store.close()

    ca = McpClient(server.mcp_port, secret_a)
    ctx = ssl._create_unverified_context()
    async with httpx.AsyncClient(verify=ctx, timeout=10) as raw:
        r = await raw.post(
            f"https://127.0.0.1:{server.mcp_port}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**MCP_HEADERS_BASE, "Authorization": f"Bearer {secret_a}"},
        )
        assert r.status_code == 401
    await ca.aclose()

    cb = McpClient(server.mcp_port, secret_b)
    try:
        await cb.initialize()
        res = await cb.call_tool("shell_exec", {"command": "echo still-ok"})
        assert res["exit_code"] == 0
    finally:
        await cb.aclose()


async def test_dashboard_api(server, env):
    ctx = ssl._create_unverified_context()
    base = f"http://127.0.0.1:{server.dashboard_port}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{base}/")
        assert r.status_code == 200
        assert "UAEG" in r.text

        r = await client.get(f"{base}/api/overview")
        assert r.status_code == 200
        assert r.json()["service"] == "UAEG"

        # create a key via dashboard (secret shown once)
        r = await client.post(f"{base}/api/keys", json={"name": "DashKey", "permission_mode": "all"})
        assert r.status_code == 201
        body = r.json()
        assert body["secret"].startswith("uaeg_")
        kid = body["key"]["key_id"]

        # list keys
        r = await client.get(f"{base}/api/keys")
        assert any(k["key_id"] == kid for k in r.json()["keys"])

        # revoke
        r = await client.post(f"{base}/api/keys/{kid}/revoke")
        assert r.json()["key"]["status"] == "revoked"

        # permissions
        r = await client.post(f"{base}/api/permissions", json={"restricted_paths": ["/etc/uaeg"]})
        assert r.status_code == 200
        r = await client.get(f"{base}/api/permissions")
        assert "/etc/uaeg" in r.json()["restricted_paths"]

        # server info
        r = await client.get(f"{base}/api/server")
        assert r.json()["mcp"]["port"] == server.mcp_port

        # sessions & logs endpoints respond
        for path in ("/api/sessions", "/api/jobs", "/api/logs"):
            r = await client.get(f"{base}{path}")
            assert r.status_code == 200


async def test_audit_log_and_restart_persistence(server, env):
    _, secret = make_key(env, "AuditAgent")
    client = McpClient(server.mcp_port, secret)
    await client.initialize()
    res = await client.call_tool("shell_exec", {"command": "id -un"})
    assert res["exit_code"] == 0
    res = await client.call_tool(
        "shell_exec", {"command": "sleep 60", "timeout_seconds": 1}
    )
    assert res["timed_out"]
    await client.aclose()

    # AT-17: audit logs carry key identity + metadata
    from uaeg import paths as uaeg_paths
    from uaeg.db import Store

    store = Store(uaeg_paths.database_path())
    logs = store.list_logs(limit=20)
    assert any(
        l["key_name"] == "AuditAgent" and l["tool"] == "shell_exec" and l["status"] == "ok"
        for l in logs
    )
    assert any(l["status"] == "timeout" for l in logs)
    store.close()

    # AT-16: restart -> config + keys persist
    server.restart()
    client = McpClient(server.mcp_port, secret)
    try:
        await client.initialize()
        res = await client.call_tool("shell_exec", {"command": "echo back"})
        assert res["exit_code"] == 0
    finally:
        await client.aclose()


async def test_job_survives_server_restart(server, env):
    """AT-11 hard mode: a running job survives a full server restart."""
    _, secret = make_key(env, "RestartAgent")
    client = McpClient(server.mcp_port, secret)
    await client.initialize()
    res = await client.call_tool(
        "shell_exec", {"command": "sleep 3 && echo survived-restart", "background": True}
    )
    job_id = res["job_id"]
    await client.aclose()

    server.restart()

    client = McpClient(server.mcp_port, secret)
    try:
        await client.initialize()
        final = await client.call_tool("job_wait", {"job_id": job_id, "timeout_seconds": 20})
        assert final["status"] == "completed"
        assert final["exit_code"] == 0
        out = await client.call_tool("job_output", {"job_id": job_id})
        assert "survived-restart" in out["data"]
    finally:
        await client.aclose()
