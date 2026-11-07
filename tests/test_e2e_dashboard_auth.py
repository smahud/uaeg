"""End-to-end dashboard authentication tests (v0.3.0).

Runs REAL ``uaeg serve`` subprocesses and REAL ``uaeg pass`` subprocesses
(detached session so getpass reads from the piped stdin) against isolated
config/data directories, covering the master-prompt acceptance matrix:

- ``uaeg pass`` first setup / mismatch retry / short+empty rejection / cancel
- login page before dashboard, correct/incorrect password, logout
- all dashboard API endpoints protected, 401/redirect semantics
- brute-force lockout over real HTTP
- password rotation revoking live sessions WITHOUT a service restart
- wildcard (0.0.0.0) bind: refused without a password, HTTPS + login when set
- HTTPS access over a non-loopback interface
- existing MCP agent keys preserved and still authenticating after every
  dashboard-auth change, restart included
- database integrity after the whole cycle
"""

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import sys
import time
import urllib.request

import httpx
import pytest

from conftest import Server, free_port, make_key, write_test_config

PASSWORD = "correct-horse-battery-staple"


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def run_pass(env, stdin: bytes, timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Run ``python -m uaeg pass`` in a detached session (no controlling
    tty), so getpass falls back to reading the piped stdin."""
    return subprocess.run(
        [sys.executable, "-m", "uaeg", "pass"],
        input=stdin,
        capture_output=True,
        env={**os.environ},
        start_new_session=True,
        timeout=timeout,
    )


import os  # noqa: E402  (used by run_pass via os.environ)


def _dashboard_client(port: int, scheme: str = "http", host: str = "127.0.0.1"):
    return httpx.Client(
        base_url=f"{scheme}://{host}:{port}",
        verify=False,
        follow_redirects=False,
        timeout=10.0,
    )


def _login(client: httpx.Client, password: str = PASSWORD):
    return client.post("/login", json={"password": password})


def _non_loopback_ip() -> str | None:
    """A real interface address (eth0) to exercise the 0.0.0.0 bind."""
    try:
        out = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=5
        ).stdout
        for token in out.split():
            if not token.startswith("127."):
                return token
    except Exception:  # noqa: BLE001
        pass
    return None


def _mcp_initialize(mcp_port: int, secret: str) -> str:
    """Raw streamable-HTTP MCP initialize with a bearer key (CI pattern)."""
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "e2e-auth-test", "version": "1"},
            },
        }
    ).encode()
    req = urllib.request.Request(
        f"https://127.0.0.1:{mcp_port}/mcp",
        data=payload,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _start_server(env):
    mcp_port, dash_port = free_port(), free_port()
    write_test_config(env, mcp_port=mcp_port, dashboard_port=dash_port)
    server = Server(env, mcp_port, dash_port)
    server.start()
    return server, mcp_port, dash_port


def _db_quick_check(env) -> str:
    from uaeg.db import Store
    from uaeg import paths

    store = Store(paths.database_path())
    try:
        return store.quick_check()
    finally:
        store.close()


# ----------------------------------------------------------------------
# `uaeg pass` CLI (no server needed)
# ----------------------------------------------------------------------
def test_pass_first_setup_and_hash_persistence(env):
    proc = run_pass(env, f"{PASSWORD}\n{PASSWORD}\n".encode())
    assert proc.returncode == 0, proc.stdout + proc.stderr.decode(errors="replace")
    out = proc.stdout.decode()
    assert "UAEG Dashboard Password Setup" in out
    assert "Password updated successfully." in out
    # prompt sequence exactly as specified
    assert "Enter new password:" in proc.stderr.decode(errors="replace")
    assert "Confirm new password:" in proc.stderr.decode(errors="replace")

    raw = (env["data"] / "dashboard-pass.json").read_text()
    assert PASSWORD not in raw
    assert "uaeg_" not in raw

    from uaeg import dashauth

    assert dashauth.password_is_configured() is True
    assert dashauth.verify_password(PASSWORD, dashauth.load_password())
    assert not dashauth.verify_password("wrong-password-123", dashauth.load_password())


def test_pass_confirmation_mismatch_rejected_then_retry(env):
    proc = run_pass(
        env,
        f"first-password-123\nmismatched-999999\n{PASSWORD}\n{PASSWORD}\n".encode(),
    )
    assert proc.returncode == 0
    out = proc.stdout.decode()
    assert "passwords do not match" in out
    assert "Password updated successfully." in out

    from uaeg import dashauth

    assert dashauth.verify_password(PASSWORD, dashauth.load_password())


def test_pass_rejects_short_and_empty_then_eof(env):
    proc = run_pass(env, b"short\ntiny\n\n\n")
    assert proc.returncode == 130  # EOF -> cancelled
    out = proc.stdout.decode()
    assert "at least 12 characters" in out
    assert "must not be empty" in out
    assert "cancelled" in out
    # nothing was written
    assert not (env["data"] / "dashboard-pass.json").exists()


def test_pass_cancel_keeps_previous_password(env):
    first = run_pass(env, f"{PASSWORD}\n{PASSWORD}\n".encode())
    assert first.returncode == 0

    # EOF immediately: cancelled, previous password stays active
    cancel = run_pass(env, b"")
    assert cancel.returncode == 130
    assert "remains active" in cancel.stdout.decode()

    from uaeg import dashauth

    assert dashauth.verify_password(PASSWORD, dashauth.load_password())


def test_pass_password_change_overwrites(env):
    assert run_pass(env, f"{PASSWORD}\n{PASSWORD}\n".encode()).returncode == 0
    assert (
        run_pass(env, b"brand-new-password-42\nbrand-new-password-42\n").returncode == 0
    )
    from uaeg import dashauth

    assert dashauth.verify_password("brand-new-password-42", dashauth.load_password())
    assert not dashauth.verify_password(PASSWORD, dashauth.load_password())


# ----------------------------------------------------------------------
# live server: full login lifecycle
# ----------------------------------------------------------------------
def test_dashboard_login_flow_with_live_server(env):
    key_id, secret = make_key(env, "microtix-agent")
    server, mcp_port, dash_port = _start_server(env)
    try:
        # 1) no password yet: legacy mode, dashboard open on the private bind
        with _dashboard_client(dash_port) as client:
            assert client.get("/").status_code == 200
            assert client.get("/api/overview").status_code == 200

        # 2) set the password while the server KEEPS RUNNING (no restart)
        assert run_pass(env, f"{PASSWORD}\n{PASSWORD}\n".encode()).returncode == 0

        with _dashboard_client(dash_port) as client:
            # 3) login page now guards the dashboard
            resp = client.get("/")
            assert resp.status_code == 302
            assert resp.headers["location"] == "/login"
            assert client.get("/login").status_code == 200

            # every API endpoint requires a session
            for path in ("/api/overview", "/api/keys", "/api/logs", "/api/server"):
                assert client.get(path).status_code == 401, path

            # 4) wrong password: generic error, no cookie
            resp = _login(client, "not-the-password-1")
            assert resp.status_code == 401
            assert resp.json()["error"] == "Login failed."
            assert client.get("/api/overview").status_code == 401

            # 5) correct password: session cookie opens everything
            resp = _login(client)
            assert resp.status_code == 200
            assert "uaeg_session" in resp.cookies
            assert client.get("/").status_code == 200
            resp = client.get("/api/keys")
            assert resp.status_code == 200
            keys = resp.json()["keys"]
            assert any(k["key_id"] == key_id for k in keys), "existing key lost!"

            # session survives a "browser refresh" (new connection, same cookie)
            cookie_value = dict(client.cookies)["uaeg_session"]
            with _dashboard_client(dash_port) as fresh:
                fresh.cookies.set("uaeg_session", cookie_value)
                assert fresh.get("/api/keys").status_code == 200

            # 6) logout revokes the session
            whoami = client.get("/api/whoami").json()
            resp = client.post(
                "/logout",
                headers={"X-CSRF-Token": whoami["csrf_token"]},
            )
            assert resp.status_code == 200
            assert client.get("/api/overview").status_code == 401

        # 7) the MCP agent key still authenticates (separate credential)
        assert "serverInfo" in _mcp_initialize(mcp_port, secret)
    finally:
        server.stop()

    # 8) database healthy after the whole cycle
    assert _db_quick_check(env) == "ok"


def test_password_rotation_revokes_sessions_without_restart(env):
    server, mcp_port, dash_port = _start_server(env)
    try:
        assert run_pass(env, f"{PASSWORD}\n{PASSWORD}\n".encode()).returncode == 0
        with _dashboard_client(dash_port) as client:
            assert _login(client).status_code == 200
            assert client.get("/api/overview").status_code == 200

            # rotate the password from a separate process (server still up)
            assert (
                run_pass(env, b"rotated-password-777\nrotated-password-777\n").returncode
                == 0
            )

            # the old session is dead immediately
            assert client.get("/api/overview").status_code == 401
            # the old password no longer works
            assert _login(client).status_code == 401
            # the new one does
            resp = _login(client, "rotated-password-777")
            assert resp.status_code == 200
            assert client.get("/api/overview").status_code == 200
    finally:
        server.stop()


def test_brute_force_lockout_over_http(env):
    server, mcp_port, dash_port = _start_server(env)
    try:
        assert run_pass(env, f"{PASSWORD}\n{PASSWORD}\n".encode()).returncode == 0
        with _dashboard_client(dash_port) as client:
            from uaeg import dashauth

            for i in range(dashauth.MAX_FAILED_ATTEMPTS):
                assert _login(client, f"bad-attempt-{i}").status_code == 401
            # locked out - even the correct password is temporarily refused
            resp = _login(client)
            assert resp.status_code == 429
            assert int(resp.headers["Retry-After"]) > 0
    finally:
        server.stop()


# ----------------------------------------------------------------------
# public binding
# ----------------------------------------------------------------------
def _serve_expect_failure(env, timeout: float = 30.0) -> subprocess.CompletedProcess:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uaeg", "serve"],
        env={**os.environ},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout=out)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise


def test_public_bind_without_password_refuses_to_start(env):
    write_test_config(
        env,
        **{
            "dashboard.bind": "0.0.0.0",
            "dashboard.allow_public": True,
            "dashboard.tls": True,
        },
    )
    proc = _serve_expect_failure(env)
    assert proc.returncode == 2
    out = proc.stdout.decode(errors="replace")
    assert "no dashboard password is set" in out
    assert "sudo uaeg pass" in out


def test_public_https_dashboard_from_non_loopback_interface(env):
    ext_ip = _non_loopback_ip()
    if not ext_ip:
        pytest.skip("no non-loopback interface available in this environment")

    key_id, secret = make_key(env, "public-agent")
    assert run_pass(env, f"{PASSWORD}\n{PASSWORD}\n".encode()).returncode == 0

    mcp_port, dash_port = free_port(), free_port()
    write_test_config(
        env,
        mcp_port=mcp_port,
        dashboard_port=dash_port,
        public_host=ext_ip,
        **{
            "dashboard.bind": "0.0.0.0",
            "dashboard.allow_public": True,
            "dashboard.tls": True,
        },
    )
    server = Server(env, mcp_port, dash_port)
    server.start()
    try:
        # HTTPS from the non-loopback interface: login page reachable
        with _dashboard_client(dash_port, scheme="https", host=ext_ip) as client:
            resp = client.get("/login")
            assert resp.status_code == 200
            assert "UAEG" in resp.text
            # unauthenticated API denied over HTTPS too
            assert client.get("/api/keys").status_code == 401
            # login issues a Secure cookie over HTTPS
            resp = _login(client)
            assert resp.status_code == 200
            assert "Secure" in resp.headers["set-cookie"]
            assert client.get("/api/keys").status_code == 200

        # plain-HTTP access to the TLS listener is refused
        with _dashboard_client(dash_port, scheme="http", host=ext_ip) as client:
            with pytest.raises(httpx.TransportError):
                client.get("/login")

        # the MCP listener is untouched by the dashboard changes
        assert "serverInfo" in _mcp_initialize(mcp_port, secret)
    finally:
        server.stop()


# ----------------------------------------------------------------------
# persistence across restart
# ----------------------------------------------------------------------
def test_restart_preserves_password_keys_and_mcp(env):
    key_id, secret = make_key(env, "survivor-agent")
    server, mcp_port, dash_port = _start_server(env)
    try:
        assert run_pass(env, f"{PASSWORD}\n{PASSWORD}\n".encode()).returncode == 0
        with _dashboard_client(dash_port) as client:
            assert _login(client).status_code == 200
            assert client.get("/api/keys").status_code == 200
        assert "serverInfo" in _mcp_initialize(mcp_port, secret)

        # restart the whole service
        server.restart()

        # password still enforced (not reset by the restart)
        with _dashboard_client(dash_port) as client:
            assert client.get("/api/keys").status_code == 401
            assert _login(client).status_code == 200
            keys = client.get("/api/keys").json()["keys"]
            assert any(k["key_id"] == key_id for k in keys)
        # agent key still authenticates after restart
        assert "serverInfo" in _mcp_initialize(mcp_port, secret)
        assert _db_quick_check(env) == "ok"
    finally:
        server.stop()
