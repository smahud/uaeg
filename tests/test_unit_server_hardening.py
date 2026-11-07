"""Regression tests: server hardening.

Covers:
- CIDR allowlist fail-closed validation (P1: invalid entries used to
  silently disable the dashboard allowlist).
- Listener supervision (P1: a crashed listener used to leave a
  half-running service).
- Config robustness (unknown fields, bool ports, str paths).
- Database schema-version guard and corruption handling.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import write_test_config

from uaeg.config import Config, ConfigError
from uaeg.dashboard import build_dashboard_asgi_app
from uaeg.db import SCHEMA_VERSION, Store
from uaeg.runtime import Runtime


# ----------------------------------------------------------------------
# CIDR fail-closed (P1)
# ----------------------------------------------------------------------
def test_config_invalid_cidr_fails_closed():
    cfg = Config()
    cfg.network.allowed_dashboard_cidrs = ["192.168.99.0/not-a-cidr"]
    with pytest.raises(ConfigError, match="invalid CIDR"):
        cfg.validate()


def test_config_all_invalid_cidrs_rejected():
    cfg = Config()
    cfg.network.allowed_dashboard_cidrs = ["bogus", "999.999.1.0/24"]
    with pytest.raises(ConfigError, match="fail|disable|invalid"):
        cfg.validate()


def test_config_cidrs_wrong_type_rejected():
    cfg = Config()
    cfg.network.allowed_dashboard_cidrs = "192.168.1.0/24"  # str, not list
    with pytest.raises(ConfigError, match="list of CIDR"):
        cfg.validate()


def test_config_valid_cidrs_pass():
    cfg = Config()
    cfg.network.allowed_dashboard_cidrs = ["192.168.1.0/24", "10.0.0.1", "fd00::/8"]
    warnings = cfg.validate()  # must not raise
    assert isinstance(warnings, list)


def test_dashboard_app_fail_closed_on_invalid_cidr(tmp_path):
    cfg = Config()
    cfg.network.allowed_dashboard_cidrs = ["nope"]
    # Programmatic construction bypasses Config.load validation; the app
    # builder must still refuse (defense in depth).
    with pytest.raises(ConfigError):
        store = Store(tmp_path / "uaeg.db")
        try:
            runtime = Runtime(cfg, store)
            build_dashboard_asgi_app(runtime)
        finally:
            store.close()


async def _asgi_call(app, path: str, client_ip: str) -> int:
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [(b"host", b"t")],
        "client": (client_ip, 51234), "server": ("127.0.0.1", 80),
    }
    status = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]

    await app(scope, receive, send)
    return status["code"]


def test_dashboard_cidr_middleware_blocks_out_of_list(tmp_path):
    cfg = Config()
    cfg.network.allowed_dashboard_cidrs = ["10.0.0.0/8"]
    cfg.dashboard.bind = "10.0.0.5"
    store = Store(tmp_path / "uaeg.db")
    try:
        runtime = Runtime(cfg, store)
        app = build_dashboard_asgi_app(runtime)
        # peer inside the allowlist -> served
        assert asyncio.run(_asgi_call(app, "/api/overview", "10.1.2.3")) == 200
        # peer outside -> 403, no content
        assert asyncio.run(_asgi_call(app, "/api/overview", "8.8.8.8")) == 403
        assert asyncio.run(_asgi_call(app, "/", "8.8.8.8")) == 403
        # non-IP peer string -> rejected (fail closed)
        assert asyncio.run(_asgi_call(app, "/api/keys", "not-an-ip")) == 403
    finally:
        store.close()


def test_dashboard_cidr_middleware_blocks_dashboard_api_paths(tmp_path):
    """The middleware protects every path, not only / and /api/..."""
    cfg = Config()
    cfg.network.allowed_dashboard_cidrs = ["127.0.0.0/8"]
    store = Store(tmp_path / "uaeg.db")
    try:
        runtime = Runtime(cfg, store)
        app = build_dashboard_asgi_app(runtime)
        assert asyncio.run(_asgi_call(app, "/api/server", "8.8.8.8")) == 403
        assert asyncio.run(_asgi_call(app, "/api/overview", "8.8.8.8")) == 403
    finally:
        store.close()


# ----------------------------------------------------------------------
# Listener supervision (P1)
# ----------------------------------------------------------------------
def test_amain_returns_nonzero_when_listener_crashes(env):
    """A listener dying with a regular exception must stop the whole service
    (previously: the service stayed half-alive and systemd was unaware)."""
    from uaeg import server as uaeg_server

    write_test_config(env)
    config = Config.load(env["config"])

    original = uaeg_server._NoSignalServer
    created = []
    orig_init = original.__init__

    def _init(self, *a, **k):
        orig_init(self, *a, **k)
        created.append(self)

    orig_serve = original.serve

    async def _serve(self):
        if self is created[1]:  # the dashboard listener (2nd constructed)
            await asyncio.sleep(0.2)
            raise RuntimeError("simulated dashboard listener crash")
        return await orig_serve(self)

    uaeg_server._NoSignalServer.__init__ = _init
    uaeg_server._NoSignalServer.serve = _serve
    try:
        rc = asyncio.run(uaeg_server._amain(config))
    finally:
        uaeg_server._NoSignalServer.__init__ = orig_init
        uaeg_server._NoSignalServer.serve = orig_serve
    assert rc == 1


def test_amain_returns_nonzero_on_bind_conflict(env):
    """Bind failure of one listener must exit non-zero (no half-running
    service)."""
    from uaeg import server as uaeg_server

    write_test_config(env)
    config = Config.load(env["config"])

    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", config.mcp.port))
    blocker.listen(1)
    try:
        rc = asyncio.run(uaeg_server._amain(config))
    finally:
        blocker.close()
    assert rc == 1


# ----------------------------------------------------------------------
# Config robustness (P3 hardening)
# ----------------------------------------------------------------------
def test_config_from_dict_rejects_unknown_fields():
    with pytest.raises(ConfigError, match="unknown or mistyped"):
        Config.from_dict({"mcp": {"port": 4001, "bogus": True}})


def test_config_rejects_bool_port():
    cfg = Config()
    cfg.mcp.port = True  # bool is an int subclass; must NOT pass as port 1
    with pytest.raises(ConfigError):
        cfg.validate()


def test_config_load_accepts_str_path(tmp_path):
    cfg = Config()
    cfg.mcp.port = 4321
    cfg.dashboard.port = 4322
    p = tmp_path / "config.json"
    cfg.save(p)
    loaded = Config.load(str(p))
    assert loaded.mcp.port == 4321


# ----------------------------------------------------------------------
# Store robustness
# ----------------------------------------------------------------------
def test_store_rejects_newer_schema_version(tmp_path):
    path = tmp_path / "uaeg.db"
    store = Store(path)
    store.close()
    # forge a database from "the future"
    db = sqlite3.connect(str(path))
    db.execute("UPDATE meta SET v=? WHERE k='schema_version'", (str(SCHEMA_VERSION + 1)))
    db.commit()
    db.close()
    with pytest.raises(sqlite3.DatabaseError, match="newer"):
        Store(path)


def test_store_quick_check_ok(tmp_path):
    store = Store(tmp_path / "uaeg.db")
    assert store.quick_check() == "ok"
    store.close()


def test_server_clean_error_on_corrupt_db(env, tmp_path):
    """serve() must exit 2 with a clean message (not a traceback) when the
    database file is corrupt, and doctor must report it."""
    write_test_config(env)
    env["data"].mkdir(parents=True, exist_ok=True)
    (env["data"] / "uaeg.db").write_bytes(b"this is not a sqlite database" * 100)

    proc = subprocess.run(
        [sys.executable, "-m", "uaeg", "serve"],
        env={**__import__("os").environ},
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2
    assert "unreadable or corrupt" in (proc.stderr + proc.stdout)
    assert "Traceback" not in (proc.stderr + proc.stdout)
