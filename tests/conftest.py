"""Shared test fixtures: isolated config/data dirs per test."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def env(tmp_path: Path):
    """Isolated UAEG_CONFIG + UAEG_DATA_DIR environment."""
    cfg_dir = tmp_path / "etc"
    data_dir = tmp_path / "var"
    cfg_dir.mkdir()
    data_dir.mkdir()
    env_vars = {
        "UAEG_CONFIG": str(cfg_dir / "config.json"),
        "UAEG_DATA_DIR": str(data_dir),
    }
    old = {k: os.environ.get(k) for k in env_vars}
    os.environ.update(env_vars)
    try:
        yield {"config": Path(env_vars["UAEG_CONFIG"]), "data": data_dir}
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def write_test_config(env, **overrides) -> Path:
    """Write a minimal valid config bound to loopback + ephemeral ports."""
    from uaeg.config import Config

    cfg = Config()
    cfg.public_host = "localhost"
    cfg.mcp.bind = "127.0.0.1"
    cfg.mcp.port = overrides.pop("mcp_port", free_port())
    cfg.dashboard.bind = "127.0.0.1"
    cfg.dashboard.port = overrides.pop("dashboard_port", free_port())
    cfg.tls.mode = "self-signed"
    for key, value in overrides.items():
        obj = cfg
        parts = key.split(".")
        for part in parts[:-1]:
            obj = getattr(obj, part)
        setattr(obj, parts[-1], value)
    cfg.save(env["config"])
    return env["config"]


def make_key(env, name: str = "tester") -> tuple[str, str]:
    """Create a key directly in the isolated store; returns (key_id, secret)."""
    from uaeg import security
    from uaeg.db import Store
    from uaeg import paths

    store = Store(paths.database_path())
    material = security.new_key(name)
    store.create_key(
        key_id=material.key_id,
        name=material.name,
        prefix=material.prefix,
        verifier_hex=material.verifier,
    )
    store.close()
    return material.key_id, material.secret


class Server:
    """A ``uaeg serve`` subprocess for e2e tests."""

    def __init__(self, env, mcp_port: int, dashboard_port: int):
        self.env = env
        self.mcp_port = mcp_port
        self.dashboard_port = dashboard_port
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uaeg", "serve"],
            env={**os.environ},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        import time
        import urllib.request
        import ssl

        deadline = time.time() + 30
        ctx = ssl._create_unverified_context()
        while time.time() < deadline:
            if self.proc.poll() is not None:
                out = self.proc.stdout.read().decode(errors="replace") if self.proc.stdout else ""
                raise RuntimeError(f"server died during startup:\n{out}")
            try:
                with urllib.request.urlopen(
                    f"https://127.0.0.1:{self.mcp_port}/healthz", timeout=2, context=ctx
                ) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("server did not become healthy in 30s")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)

    def restart(self) -> None:
        self.stop()
        self.start()
