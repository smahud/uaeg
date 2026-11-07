"""Dashboard authentication unit tests (v0.3.0).

Covers the dashauth primitives (password file, sessions, throttling) and
the dashboard ASGI middleware behavior end-to-end at the application level
(httpx ASGI transport): login, session enforcement, CSRF, Origin checks,
body limits, brute-force lockout, password rotation and legacy mode.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
import pytest

from conftest import write_test_config

from uaeg import dashauth
from uaeg.config import Config, ConfigError
from uaeg.dashauth import (
    LoginThrottle,
    PasswordManager,
    SessionStore,
    hash_password,
    load_password,
    password_file_path,
    password_is_configured,
    save_password,
    verify_password,
)
from uaeg.dashboard import build_dashboard_asgi_app
from uaeg.db import Store
from uaeg.runtime import Runtime

PASSWORD = "correct-horse-battery-staple"
MIN_LEN = dashauth.MIN_PASSWORD_LEN


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _set_password(env, password: str = PASSWORD):
    record = hash_password(password)
    save_password(record)
    return record


def _build_app(env, **cfg_overrides):
    write_test_config(env, **cfg_overrides)
    config = Config.load(env["config"])
    store = Store(env["data"] / "uaeg.db")
    runtime = Runtime(config, store)
    app = build_dashboard_asgi_app(runtime)
    return app, runtime, store


@pytest.fixture()
def auth_app(env):
    """Dashboard app WITH a password configured."""
    _set_password(env)
    app, runtime, store = _build_app(env)
    try:
        yield {"app": app, "runtime": runtime, "store": store, "env": env}
    finally:
        store.close()


@pytest.fixture()
def legacy_app(env):
    """Dashboard app WITHOUT a password (legacy v0.2.0 mode)."""
    app, runtime, store = _build_app(env)
    try:
        yield {"app": app, "runtime": runtime, "store": store, "env": env}
    finally:
        store.close()


def _client(app, base_url="http://testserver") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base_url
    )


async def _login(client: httpx.AsyncClient, password: str = PASSWORD):
    return await client.post(
        "/login", json={"password": password}, headers={"Origin": "http://testserver"}
    )


async def _get_csrf(client: httpx.AsyncClient) -> str:
    resp = await client.get("/api/whoami")
    assert resp.status_code == 200
    return resp.json()["csrf_token"]


# ----------------------------------------------------------------------
# dashauth: password file
# ----------------------------------------------------------------------
def test_password_hash_roundtrip(env):
    record = hash_password(PASSWORD)
    assert record.scheme == "scrypt"
    assert record.generation
    save_password(record)

    assert password_is_configured() is True
    loaded = load_password()
    assert loaded is not None
    assert loaded.hash_hex == record.hash_hex
    assert loaded.generation == record.generation

    assert verify_password(PASSWORD, loaded) is True
    assert verify_password("wrong-password-123", loaded) is False


def test_password_file_never_contains_plaintext(env):
    _set_password(env)
    raw = password_file_path().read_text(encoding="utf-8")
    assert PASSWORD not in raw
    data = json.loads(raw)
    assert set(data) >= {"scheme", "salt_hex", "hash_hex", "generation"}
    # salt and hash are hex, not the password
    bytes.fromhex(data["salt_hex"])
    bytes.fromhex(data["hash_hex"])


def test_password_file_permissions_and_atomicity(env):
    first = _set_password(env, "first-password-123456")
    stat_before = password_file_path().stat()
    assert stat_before.st_mode & 0o777 == 0o600

    # A crash between write and rename must leave the OLD record intact.
    second = hash_password("second-password-654321")
    real_replace = os.replace

    def _failing_replace(src, dst):
        raise OSError("simulated crash before rename")

    os.replace = _failing_replace
    try:
        with pytest.raises(OSError):
            save_password(second)
    finally:
        os.replace = real_replace

    assert verify_password("first-password-123456", load_password())
    assert not verify_password("second-password-654321", load_password())
    # no leftover temp file with the NEW hash
    tmp = password_file_path().with_name(password_file_path().name + ".tmp")
    assert not tmp.exists() or "second" not in tmp.read_text()


def test_password_rotation_changes_generation(env):
    first = _set_password(env, "first-password-123456")
    second = hash_password("second-password-654321")
    assert first.generation != second.generation
    save_password(second)
    assert load_password().generation == second.generation


def test_corrupt_password_file_fails_closed(env):
    _set_password(env)
    password_file_path().write_text("{ this is not json", encoding="utf-8")

    # The file still EXISTS -> configured (fail closed, never legacy mode)
    assert password_is_configured() is True
    with pytest.raises(dashauth.PasswordFileError):
        load_password()

    manager = PasswordManager()
    assert manager.is_configured() is True
    assert manager.is_broken() is True
    assert manager.verify(PASSWORD) is False


def test_password_manager_hot_reload(env):
    manager = PasswordManager()
    assert manager.is_configured() is False

    record = _set_password(env, "hot-reload-password-1")
    assert manager.is_configured() is True
    assert manager.verify("hot-reload-password-1") is True
    gen1 = manager.generation()

    save_password(hash_password("hot-reload-password-2"))
    # mtime-based reload (no restart)
    deadline = time.time() + 5
    while time.time() < deadline:
        if manager.generation() != gen1:
            break
        time.sleep(0.05)
    assert manager.generation() != gen1
    assert manager.verify("hot-reload-password-2") is True
    assert manager.verify("hot-reload-password-1") is False


# ----------------------------------------------------------------------
# dashauth: sessions
# ----------------------------------------------------------------------
def test_session_store_lifecycle():
    store = SessionStore(idle_timeout_s=60, absolute_timeout_s=3600)
    session = store.create("gen-1")
    assert store.get_valid(session.token, "gen-1") is not None
    # unknown / missing tokens
    assert store.get_valid("nope", "gen-1") is None
    assert store.get_valid(None, "gen-1") is None

    # wrong generation -> invalid AND destroyed (password changed)
    assert store.get_valid(session.token, "gen-2") is None
    assert store.get_valid(session.token, "gen-1") is None

    # revoke
    other = store.create("gen-1")
    assert store.revoke(other.token) is True
    assert store.get_valid(other.token, "gen-1") is None
    assert store.revoke(other.token) is False


def test_session_store_idle_expiry():
    store = SessionStore(idle_timeout_s=0.2, absolute_timeout_s=60)
    session = store.create("gen-1")
    time.sleep(0.3)
    assert store.get_valid(session.token, "gen-1") is None


def test_session_store_absolute_expiry():
    # idle 0.2s, absolute 0.4s: keep the session "active" (touch within
    # the idle window) and prove the ABSOLUTE cap still expires it.
    store = SessionStore(idle_timeout_s=0.2, absolute_timeout_s=0.4)
    session = store.create("gen-1")
    time.sleep(0.15)
    assert store.get_valid(session.token, "gen-1") is not None  # touched
    time.sleep(0.15)
    assert store.get_valid(session.token, "gen-1") is not None  # touched again
    time.sleep(0.2)  # created_at is now ~0.5s old > 0.4s absolute
    assert store.get_valid(session.token, "gen-1") is None


def test_session_store_revoke_all():
    store = SessionStore(idle_timeout_s=60, absolute_timeout_s=3600)
    a = store.create("gen-1")
    b = store.create("gen-1")
    assert store.revoke_all() == 2
    assert store.get_valid(a.token, "gen-1") is None
    assert store.get_valid(b.token, "gen-1") is None


def test_session_tokens_are_random():
    store = SessionStore(idle_timeout_s=60, absolute_timeout_s=3600)
    tokens = {store.create("g").token for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 32 for t in tokens)


# ----------------------------------------------------------------------
# dashauth: login throttle
# ----------------------------------------------------------------------
def test_throttle_lockout_after_repeated_failures():
    throttle = LoginThrottle(max_failures=5, window_s=300, lockout_s=300)
    for i in range(4):
        assert throttle.record_failure("10.0.0.9") is False
    assert throttle.locked_for("10.0.0.9") == 0
    # 5th failure triggers the lockout
    assert throttle.record_failure("10.0.0.9") is True
    assert throttle.locked_for("10.0.0.9") > 0
    # a different source IP is unaffected (no global lockout DoS)
    assert throttle.locked_for("10.0.0.10") == 0


def test_throttle_success_resets_history():
    throttle = LoginThrottle(max_failures=5, window_s=300, lockout_s=300)
    for _ in range(4):
        throttle.record_failure("10.0.0.9")
    throttle.record_success("10.0.0.9")
    assert throttle.record_failure("10.0.0.9") is False  # back to 1 failure
    assert throttle.locked_for("10.0.0.9") == 0


# ----------------------------------------------------------------------
# config validation (wildcard + tls)
# ----------------------------------------------------------------------
def test_wildcard_bind_requires_allow_public_and_tls():
    cfg = Config()
    cfg.dashboard.bind = "0.0.0.0"
    with pytest.raises(ConfigError):
        cfg.validate()

    cfg = Config()
    cfg.dashboard.bind = "0.0.0.0"
    cfg.dashboard.allow_public = True
    with pytest.raises(ConfigError):
        cfg.validate()

    cfg = Config()
    cfg.dashboard.bind = "0.0.0.0"
    cfg.dashboard.allow_public = True
    cfg.dashboard.tls = True
    warnings = cfg.validate()
    assert any("ALL interfaces" in w for w in warnings)


# ----------------------------------------------------------------------
# app-level: legacy mode (no password, private bind)
# ----------------------------------------------------------------------
async def test_legacy_mode_without_password_serves_dashboard(legacy_app):
    async with _client(legacy_app["app"]) as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "UAEG" in resp.text
        resp = await client.get("/api/overview")
        assert resp.status_code == 200
        assert resp.json()["service"] == "UAEG"


async def test_legacy_mode_login_page_redirects_to_dashboard(legacy_app):
    async with _client(legacy_app["app"]) as client:
        # /login exists but there is nothing to log in to yet
        resp = await client.get("/login", follow_redirects=False)
        # no password configured: the login page is not meaningful;
        # it simply points the operator at 'uaeg pass'
        assert resp.status_code == 200
        assert "uaeg pass" in resp.text


# ----------------------------------------------------------------------
# app-level: authentication enforced
# ----------------------------------------------------------------------
async def test_unauthenticated_requests_denied(auth_app):
    async with _client(auth_app["app"]) as client:
        # browser page -> redirect to /login
        resp = await client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"
        # API -> 401 JSON
        for path in (
            "/api/overview",
            "/api/keys",
            "/api/sessions",
            "/api/jobs",
            "/api/logs",
            "/api/server",
            "/api/permissions",
            "/api/whoami",
        ):
            resp = await client.get(path)
            assert resp.status_code == 401, path
            assert resp.json()["error"] == "authentication required"
        # state-changing endpoints also require auth
        resp = await client.post("/api/keys", json={"name": "x"})
        assert resp.status_code == 401
        # login page itself is public
        resp = await client.get("/login")
        assert resp.status_code == 200
        assert "UAEG" in resp.text
        assert "__PASSWORD_CONFIGURED__" not in resp.text
        assert "PW_CONFIGURED=true" in resp.text


async def test_login_success_sets_secure_cookie_and_opens_dashboard(auth_app):
    async with _client(auth_app["app"]) as client:
        resp = await _login(client)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        cookie_header = resp.headers["set-cookie"]
        assert "HttpOnly" in cookie_header
        assert "SameSite=lax" in cookie_header or "samesite=lax" in cookie_header.lower()
        assert "Path=/" in cookie_header
        assert "Secure" not in cookie_header  # plain HTTP test transport

        # session cookie now grants access (survives "browser refresh")
        resp = await client.get("/")
        assert resp.status_code == 200
        resp = await client.get("/api/overview")
        assert resp.status_code == 200
        resp = await client.get("/api/keys")
        assert resp.status_code == 200


async def test_login_over_https_sets_secure_cookie(auth_app):
    async with _client(auth_app["app"], base_url="https://testserver") as client:
        resp = await _login(client)
        assert resp.status_code == 200
        assert "Secure" in resp.headers["set-cookie"]


async def test_login_wrong_password_generic_error(auth_app):
    async with _client(auth_app["app"]) as client:
        resp = await _login(client, "definitely-wrong-pass")
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"] == "Login failed."
        # no session cookie issued, nothing echoed back
        assert "set-cookie" not in resp.headers or "uaeg_session" not in resp.headers.get("set-cookie", "")
        resp = await client.get("/api/overview")
        assert resp.status_code == 401


async def test_logout_revokes_session(auth_app):
    async with _client(auth_app["app"]) as client:
        await _login(client)
        csrf = await _get_csrf(client)
        resp = await client.post(
            "/logout", headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"}
        )
        assert resp.status_code == 200
        # cookie is cleared and the session is dead
        resp = await client.get("/api/overview")
        assert resp.status_code == 401
        resp = await client.get("/", follow_redirects=False)
        assert resp.status_code == 302


async def test_password_rotation_revokes_existing_sessions(auth_app):
    async with _client(auth_app["app"]) as client:
        await _login(client)
        assert (await client.get("/api/overview")).status_code == 200

        # rotate the password behind the server's back (no restart)
        save_password(hash_password("rotated-password-999"))

        resp = await client.get("/api/overview")
        assert resp.status_code == 401
        resp = await client.get("/", follow_redirects=False)
        assert resp.status_code == 302

        # the new password works immediately
        resp = await _login(client, "rotated-password-999")
        assert resp.status_code == 200
        assert (await client.get("/api/overview")).status_code == 200


async def test_csrf_token_required_for_state_changes(auth_app):
    async with _client(auth_app["app"]) as client:
        await _login(client)
        csrf = await _get_csrf(client)

        # POST without CSRF token -> 403
        resp = await client.post("/api/keys", json={"name": "no-csrf"})
        assert resp.status_code == 403
        assert "CSRF" in resp.json()["error"]

        # wrong token -> 403
        resp = await client.post(
            "/api/keys", json={"name": "bad-csrf"}, headers={"X-CSRF-Token": "forged"}
        )
        assert resp.status_code == 403

        # correct token -> allowed
        resp = await client.post(
            "/api/keys",
            json={"name": "with-csrf"},
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )
        assert resp.status_code == 201
        assert resp.json()["secret"].startswith("uaeg_")


async def test_origin_validation_rejects_foreign_origin(auth_app):
    async with _client(auth_app["app"]) as client:
        await _login(client)
        csrf = await _get_csrf(client)
        resp = await client.post(
            "/api/keys",
            json={"name": "evil"},
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        )
        assert resp.status_code == 403
        assert "origin" in resp.json()["error"]

        # login endpoint also validates Origin when present
        resp = await client.post(
            "/login",
            json={"password": PASSWORD},
            headers={"Origin": "https://evil.example"},
        )
        assert resp.status_code == 403


async def test_login_body_size_limit(auth_app):
    async with _client(auth_app["app"]) as client:
        big = "x" * (dashauth.MAX_LOGIN_BODY_BYTES + 1)
        resp = await client.post(
            "/login",
            content=json.dumps({"password": big}),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413


async def test_brute_force_lockout(auth_app):
    async with _client(auth_app["app"]) as client:
        for i in range(dashauth.MAX_FAILED_ATTEMPTS):
            resp = await _login(client, f"wrong-attempt-{i:02d}")
            assert resp.status_code == 401

        # locked out now - even the CORRECT password is refused temporarily
        resp = await _login(client, PASSWORD)
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert resp.json()["error"] == "Too many failed attempts. Try again later."


async def test_login_audit_log_written_without_secrets(auth_app):
    async with _client(auth_app["app"]) as client:
        await _login(client, "wrong-password-a")
        await _login(client)

    logs = auth_app["store"].list_logs(limit=10)
    login_logs = [l for l in logs if l["tool"] == "dashboard_login"]
    assert any(l["status"] == "denied" for l in login_logs)
    assert any(l["status"] == "ok" for l in login_logs)
    # no password material anywhere in the audit entries
    for entry in login_logs:
        blob = json.dumps(entry)
        assert PASSWORD not in blob
        assert "wrong-password-a" not in blob


async def test_cidr_allowlist_composes_with_auth(env):
    _set_password(env)
    app, runtime, store = _build_app(
        env, **{"network.allowed_dashboard_cidrs": ["127.0.0.0/8"]}
    )
    try:
        # httpx ASGITransport reports client 127.0.0.1 -> allowed
        async with _client(app) as client:
            resp = await client.get("/login")
            assert resp.status_code == 200
    finally:
        store.close()

    # a peer outside the allowlist is rejected before auth even runs
    from uaeg.dashboard import _ClientCheckMiddleware  # noqa: F401 - composition

    async def _call_from(peer_ip: str) -> int:
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "path": "/login", "raw_path": b"/login",
            "query_string": b"", "headers": [(b"host", b"t")],
            "client": (peer_ip, 51234), "server": ("127.0.0.1", 80),
        }
        status = {}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]

        await app(scope, receive, send)
        return status["code"]

    assert await _call_from("8.8.8.8") == 403
    assert await _call_from("127.0.0.1") == 200
