"""End-to-end regression: the CORRECTED production recovery path (2026-11-09).

Reproduces the operator scenario from ``report/PRODUCTION_RECOVERY_V041.md``
(revision 2) against a REAL isolated installation — the same root-check-
disabled, directory-redirected installer technique the CI ``installer`` job
and the upgrade-contract suite use (no root, no systemd, no system paths):

1. fresh install (loopback dashboard, manual TLS, ports 3902/3903);
2. create persistent state: dashboard password, MCP agent key, custom
   config value, database rows;
3. run the EXACT corrected repair invocation from the runbook::

       bash install.sh --repair \\
         --dashboard-bind public \\
         --cert <fullchain.pem> --key <privkey.pem>

4. assert the effective configuration is the target production shape
   (mcp 0.0.0.0:3902, dashboard 0.0.0.0:3903 + allow_public + tls,
   tls.mode=manual with the given files) while every unrelated value
   and ALL persistent state (password, agent key, database) survive;
5. serve from the repaired installation and verify over REAL HTTPS:
   login page first, unauthenticated API refused, correct password in,
   wrong password out, logout revokes, and the PRE-REPAIR MCP agent key
   still authenticates on 3902 while the dashboard serves on 3903
   (both listeners live simultaneously).
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "install.sh"

PASSWORD = "correct-horse-battery-staple-e2e"
MCP_PORT = 3902
DASH_PORT = 3903

_UNVERIFIED = ssl._create_unverified_context()


def _ports_free() -> bool:
    for port in (MCP_PORT, DASH_PORT):
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", port))
            except OSError:
                return False
    return True


pytestmark = pytest.mark.skipif(
    not _ports_free(), reason=f"ports {MCP_PORT}/{DASH_PORT} not available"
)


# ----------------------------------------------------------------------
# Fixtures: patched installer + isolated base directory + TLS material
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def base(tmp_path_factory) -> Path:
    b = tmp_path_factory.mktemp("uaeg-repair-e2e")
    (b / "bin").mkdir()
    (b / "sbin").mkdir()
    return b


@pytest.fixture(scope="module")
def tls_pair(base) -> tuple[Path, Path]:
    cert = base / "fullchain.pem"
    key = base / "privkey.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "5",
         "-nodes", "-subj", "/CN=host.zerotier.my.id",
         "-addext", "subjectAltName=DNS:host.zerotier.my.id,IP:127.0.0.1",
         "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )
    return cert, key


@pytest.fixture(scope="module")
def installer(base) -> Path:
    """Root-check-disabled installer copy (CI ``installer`` job patch)."""
    patched = base / "install-test.sh"
    proc = subprocess.run(
        ["sed",
         "-e", r"s|^if \[\[ \$EUID -ne 0 \]\]; then|if false; then|",
         "-e", r's|^  die "this installer must run as root.*|  :|',
         "-e", r"s|^apt-get update -qq$|:|",
         "-e", r"s|^apt-get install -y -qq python3 python3-venv openssl curl >/dev/null$|:|",
         "-e", rf's|^ln -sf "\$VENV_DIR/bin/uaeg" /usr/local/bin/uaeg$|ln -sf "$VENV_DIR/bin/uaeg" "{base}/bin/uaeg"|',
         "-e", rf"s|/usr/local/sbin/uaeg-upgrade|{base}/sbin/uaeg-upgrade|g",
         str(INSTALLER)],
        capture_output=True, text=True, check=True,
    )
    patched.write_text(proc.stdout)
    return patched


class Install:
    """Handles for the isolated installation under test."""

    def __init__(self, base: Path, cert: Path, key: Path):
        self.base = base
        self.cert, self.key = cert, key
        self.install_dir = base / "opt" / "uaeg"
        self.config_dir = base / "etc" / "uaeg"
        self.data_dir = base / "var" / "lib" / "uaeg"
        self.config_file = self.config_dir / "config.json"
        self.venv_python = self.install_dir / ".venv" / "bin" / "python"

    def target_args(self) -> list[str]:
        return [
            "--install-dir", str(self.install_dir),
            "--config-dir", str(self.config_dir),
            "--data-dir", str(self.data_dir),
            "--no-service", "--yes",
        ]

    def run_installer(self, script: Path, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "UAEG_SYSTEM_UAEG_BIN": str(self.base / "bin" / "uaeg")}
        return subprocess.run(
            ["bash", str(script), *args],
            capture_output=True, text=True, timeout=420, cwd=str(REPO), env=env,
        )

    def uaeg(self, *args: str, stdin: bytes = b"") -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "UAEG_CONFIG": str(self.config_file),
            "UAEG_DATA_DIR": str(self.data_dir),
        }
        proc = subprocess.run(
            [str(self.venv_python), "-m", "uaeg", *args],
            input=stdin, capture_output=True, timeout=120,
            start_new_session=True, env=env,
        )
        proc.stdout = proc.stdout.decode("utf-8", errors="replace")  # type: ignore[assignment]
        proc.stderr = proc.stderr.decode("utf-8", errors="replace")  # type: ignore[assignment]
        return proc  # type: ignore[return-value]

    def config(self) -> dict:
        return json.loads(self.config_file.read_text(encoding="utf-8"))

    def key_rows(self) -> list[tuple]:
        con = sqlite3.connect(str(self.data_dir / "uaeg.db"))
        try:
            return con.execute(
                "SELECT key_id, name, prefix, verifier, status, permission_mode"
                " FROM keys ORDER BY key_id"
            ).fetchall()
        finally:
            con.close()

    def db_quick_check(self) -> str:
        con = sqlite3.connect(str(self.data_dir / "uaeg.db"))
        try:
            return con.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            con.close()


@pytest.fixture(scope="module")
def install(base, tls_pair) -> Install:
    cert, key = tls_pair
    return Install(base, cert, key)


# ----------------------------------------------------------------------
# Phase 1-3: install -> state -> REPAIR (the corrected runbook command)
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def repaired(installer, install) -> Install:
    # -- Phase 1: fresh install (loopback dashboard, manual TLS) --
    proc = install.run_installer(
        installer,
        "--port", str(MCP_PORT), "--dashboard-port", str(DASH_PORT),
        "--dashboard-bind", "loopback",
        "--public-ip", "127.0.0.1",
        "--cert", str(install.cert), "--key", str(install.key),
        *install.target_args(),
    )
    assert proc.returncode == 0, proc.stderr
    assert install.config()["dashboard"]["bind"] == "127.0.0.1"

    # -- Phase 2: persistent state (password, agent key, custom value) --
    proc = install.uaeg("pass", stdin=(f"{PASSWORD}\n{PASSWORD}\n").encode())
    assert proc.returncode == 0, proc.stderr
    proc = install.uaeg("generate", "--name", "pre-repair-key")
    assert proc.returncode == 0, proc.stderr
    secret = next(
        line.strip() for line in proc.stdout.splitlines()
        if line.strip().startswith("uaeg_")
    )
    install.secret = secret  # type: ignore[attr-defined]
    assert install.uaeg("config", "set", "exec.default_timeout_s", "333").returncode == 0

    install.password_before = (install.data_dir / "dashboard-pass.json").read_bytes()  # type: ignore[attr-defined]
    install.keys_before = install.key_rows()  # type: ignore[attr-defined]

    # -- Phase 3: the CORRECTED recovery invocation from the runbook --
    proc = install.run_installer(
        installer,
        "--repair",
        "--dashboard-bind", "public",
        "--cert", str(install.cert),
        "--key", str(install.key),
        *install.target_args(),
    )
    assert proc.returncode == 0, proc.stderr
    assert "public dashboard: password verified" in proc.stdout
    return install


# ----------------------------------------------------------------------
# Phase 4: the target production configuration, nothing else changed
# ----------------------------------------------------------------------
def test_repair_produced_target_production_configuration(repaired):
    cfg = repaired.config()
    assert cfg["mcp"]["bind"] == "0.0.0.0"
    assert cfg["mcp"]["port"] == MCP_PORT
    assert cfg["dashboard"]["bind"] == "0.0.0.0"
    assert cfg["dashboard"]["port"] == DASH_PORT
    assert cfg["dashboard"]["allow_public"] is True
    assert cfg["dashboard"]["tls"] is True
    assert cfg["tls"]["mode"] == "manual"
    assert cfg["tls"]["cert"] == str(repaired.cert)
    assert cfg["tls"]["key"] == str(repaired.key)
    # Unrelated settings survive the repair untouched.
    assert cfg["exec"]["default_timeout_s"] == 333
    assert cfg["public_host"] == "127.0.0.1"


# ----------------------------------------------------------------------
# Phase 5: zero data loss across the repair
# ----------------------------------------------------------------------
def test_repair_preserved_password_keys_and_database(repaired):
    # Dashboard password: byte-identical file (same hash AND generation).
    after = (repaired.data_dir / "dashboard-pass.json").read_bytes()
    assert after == repaired.password_before  # type: ignore[attr-defined]
    # MCP agent keys: identical rows.
    assert repaired.key_rows() == repaired.keys_before  # type: ignore[attr-defined]
    # Database integrity.
    assert repaired.db_quick_check() == "ok"


# ----------------------------------------------------------------------
# Phase 6: serve the repaired installation and verify the full flow
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def serving(repaired):
    env = {
        **os.environ,
        "UAEG_CONFIG": str(repaired.config_file),
        "UAEG_DATA_DIR": str(repaired.data_dir),
    }
    proc = subprocess.Popen(
        [str(repaired.venv_python), "-m", "uaeg", "serve"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 60
        up = {"mcp": False, "dash": False}
        while time.time() < deadline and not all(up.values()):
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                raise RuntimeError(f"repaired server died during startup:\n{out}")
            if not up["mcp"]:
                try:
                    with urllib.request.urlopen(
                        f"https://127.0.0.1:{MCP_PORT}/healthz", timeout=2,
                        context=_UNVERIFIED,
                    ) as resp:
                        up["mcp"] = resp.status == 200
                except Exception:
                    pass
            if not up["dash"]:
                try:
                    with urllib.request.urlopen(
                        f"https://127.0.0.1:{DASH_PORT}/login", timeout=2,
                        context=_UNVERIFIED,
                    ) as resp:
                        up["dash"] = resp.status == 200
                except Exception:
                    pass
            if not all(up.values()):
                time.sleep(0.25)
        assert all(up.values()), (
            f"listeners did not come up: {up} (mcp={MCP_PORT}, dash={DASH_PORT})"
        )
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=f"https://127.0.0.1:{DASH_PORT}",
        verify=False, follow_redirects=False, timeout=15.0,
    )


def test_login_page_served_first_over_https(serving):
    with _client() as c:
        resp = c.get("/login")
        assert resp.status_code == 200
        assert "password" in resp.text.lower()
        # TLS is really on (handshake succeeded over https with our cert).
        assert resp.request.url.scheme == "https"


def test_unauthenticated_api_refused(serving):
    with _client() as c:
        resp = c.get("/api/overview")
        assert resp.status_code in (401, 403)
        assert "UAEG" not in resp.text  # no dashboard data leaks


def test_correct_password_grants_access_wrong_password_rejected(serving):
    with _client() as c:
        bad = c.post("/login", json={"password": "wrong-password-attempt-123"})
        assert bad.status_code == 401
        assert bad.text.lower().find("login failed") != -1
        assert not bad.cookies

        good = c.post("/login", json={"password": PASSWORD})
        assert good.status_code == 200
        cookie_header = good.headers.get("set-cookie", "")
        assert "httponly" in cookie_header.lower()
        assert "secure" in cookie_header.lower()
        assert "samesite=lax" in cookie_header.lower()

        resp = c.get("/api/overview")  # session cookie now in the jar
        assert resp.status_code == 200
        assert "UAEG" in resp.text


def test_logout_revokes_the_session(serving):
    with _client() as c:
        assert c.post("/login", json={"password": PASSWORD}).status_code == 200
        who = c.get("/api/whoami")
        assert who.status_code == 200
        csrf = who.json()["csrf_token"]
        out = c.post("/logout", headers={"X-CSRF-Token": csrf})
        assert out.status_code == 200
        # The revoked session cookie no longer opens the API.
        after = c.get("/api/overview")
        assert after.status_code in (401, 403)


def test_pre_repair_mcp_key_still_authenticates_on_3902(serving, repaired):
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "repair-e2e", "version": "1"},
        },
    }).encode()
    # Without the key: authentication required.
    req = urllib.request.Request(
        f"https://127.0.0.1:{MCP_PORT}/mcp", data=payload,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10, context=_UNVERIFIED)
        raise AssertionError("unauthenticated MCP call must not succeed")
    except urllib.error.HTTPError as exc:
        assert exc.code in (401, 403)

    # With the PRE-REPAIR agent key: works (credentials preserved).
    req = urllib.request.Request(
        f"https://127.0.0.1:{MCP_PORT}/mcp", data=payload,
        headers={
            "Authorization": f"Bearer {repaired.secret}",  # type: ignore[attr-defined]
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10, context=_UNVERIFIED) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        assert resp.status == 200
        assert "serverInfo" in body


def test_both_listeners_operational_simultaneously(serving):
    """MCP 3902 and dashboard 3903 answer at the same time (one process)."""
    with urllib.request.urlopen(
        f"https://127.0.0.1:{MCP_PORT}/healthz", timeout=5, context=_UNVERIFIED
    ) as mcp_resp:
        health = json.loads(mcp_resp.read().decode())
    with _client() as c:
        dash = c.get("/login")
    assert health.get("status") == "ok"
    assert health.get("version") == "0.4.1"
    assert dash.status_code == 200


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
