"""Unit regression tests for the 2026-11-09 installer-contract audit.

Locks two verified operator-path defects at the unit level:

1. **`uaeg config set` ordering contract** (runbook revision 1 defect):
   every ``config set`` re-validates the WHOLE configuration, so the
   wildcard dashboard bind must be set LAST.  Setting ``dashboard.bind
   0.0.0.0`` first is rejected and NOT saved — following the runbook's
   old order silently left the dashboard on 127.0.0.1.

2. **`uaeg pass` rotation semantics**: rotation revokes dashboard sessions
   (generation bump) while MCP agent keys and the database stay untouched
   (separate credential domains).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

from conftest import make_key, write_test_config

PASSWORD_1 = "correct-horse-battery-staple-one"
PASSWORD_2 = "correct-horse-battery-staple-two"


def run_cli(env, *args: str, stdin: bytes = b"") -> subprocess.CompletedProcess:
    """Run the uaeg CLI (real subprocess) against the isolated env."""
    proc = subprocess.run(
        [sys.executable, "-m", "uaeg", *args],
        input=stdin,
        capture_output=True,
        env={**os.environ},
        start_new_session=True,  # no controlling tty: getpass reads stdin
        timeout=60,
    )
    proc.stdout = proc.stdout.decode("utf-8", errors="replace")
    proc.stderr = proc.stderr.decode("utf-8", errors="replace")
    return proc


# ----------------------------------------------------------------------
# 1. `uaeg config set` ordering contract (wildcard bind LAST)
# ----------------------------------------------------------------------
def test_config_set_wildcard_bind_first_is_rejected_and_not_saved(env):
    write_test_config(env)
    proc = run_cli(env, "config", "set", "dashboard.bind", "0.0.0.0")
    assert proc.returncode == 1
    assert "Refusing to bind the dashboard" in proc.stderr + proc.stdout
    # The rejected value must NOT be persisted.
    proc = run_cli(env, "config", "get", "dashboard.bind")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "127.0.0.1"


def test_config_set_public_dashboard_sequence_bind_last_succeeds(env):
    """The corrected runbook order: allow_public -> tls -> bind (LAST)."""
    write_test_config(env)
    assert run_cli(env, "config", "set", "dashboard.allow_public", "true").returncode == 0
    assert run_cli(env, "config", "set", "dashboard.tls", "true").returncode == 0
    proc = run_cli(env, "config", "set", "dashboard.bind", "0.0.0.0")
    assert proc.returncode == 0

    data = json.loads(env["config"].read_text(encoding="utf-8"))
    assert data["dashboard"]["bind"] == "0.0.0.0"
    assert data["dashboard"]["allow_public"] is True
    assert data["dashboard"]["tls"] is True
    # Ports and unrelated settings survive every rewrite.
    assert data["mcp"]["port"] == json.loads(
        run_cli(env, "config", "get", "mcp.port").stdout
    )


def test_config_set_manual_tls_validates_files(env, tmp_path):
    """Manual TLS is only accepted once a real cert+key pair exists."""
    write_test_config(env)

    # mode=manual without cert/key: rejected, not saved.
    assert run_cli(env, "config", "set", "tls.mode", "manual").returncode == 1
    assert run_cli(env, "config", "get", "tls.mode").stdout.strip() == "self-signed"

    # Paths to files that do not exist: still rejected once mode goes manual.
    run_cli(env, "config", "set", "tls.cert", str(tmp_path / "nope.crt"))
    run_cli(env, "config", "set", "tls.key", str(tmp_path / "nope.key"))
    assert run_cli(env, "config", "set", "tls.mode", "manual").returncode == 1
    assert run_cli(env, "config", "get", "tls.mode").stdout.strip() == "self-signed"

    # A real pair: accepted (fail-open only for genuinely valid material).
    import subprocess as _sp

    cert, key = tmp_path / "ok.crt", tmp_path / "ok.key"
    _sp.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "2",
         "-nodes", "-subj", "/CN=host.zerotier.my.id", "-keyout", str(key),
         "-out", str(cert)],
        check=True, capture_output=True,
    )
    run_cli(env, "config", "set", "tls.cert", str(cert))
    run_cli(env, "config", "set", "tls.key", str(key))
    assert run_cli(env, "config", "set", "tls.mode", "manual").returncode == 0
    assert run_cli(env, "config", "get", "tls.mode").stdout.strip() == "manual"


# ----------------------------------------------------------------------
# 2. `uaeg pass` rotation: sessions revoked, MCP keys untouched
# ----------------------------------------------------------------------
def _password_record(env):
    from uaeg import dashauth

    dashauth.paths  # ensure import side effects
    old = os.environ.get("UAEG_DATA_DIR")
    try:
        os.environ["UAEG_DATA_DIR"] = str(env["data"])
        rec = dashauth.load_password()
    finally:
        if old is None:
            os.environ.pop("UAEG_DATA_DIR", None)
        else:
            os.environ["UAEG_DATA_DIR"] = old
    return rec


def test_uaeg_pass_setup_and_rotation_revokes_sessions_keeps_keys(env):
    write_test_config(env)
    from uaeg import dashauth

    # -- setup (interactive, hidden, twice) --
    proc = run_cli(env, "pass", stdin=(f"{PASSWORD_1}\n{PASSWORD_1}\n").encode())
    assert proc.returncode == 0, proc.stderr
    assert "Password updated successfully" in proc.stdout
    assert PASSWORD_1 not in proc.stdout  # never echoed
    rec1 = _password_record(env)
    assert rec1 is not None
    assert dashauth.verify_password(PASSWORD_1, rec1)

    # -- an MCP agent key exists --
    key_id, secret = make_key(env, "audit-key")

    # -- an active dashboard session under generation 1 --
    sessions = dashauth.SessionStore(idle_timeout_s=60, absolute_timeout_s=3600)
    session = sessions.create(rec1.generation)
    assert sessions.get_valid(session.token, rec1.generation) is not None

    # -- rotate the password --
    proc = run_cli(env, "pass", stdin=(f"{PASSWORD_2}\n{PASSWORD_2}\n").encode())
    assert proc.returncode == 0, proc.stderr
    rec2 = _password_record(env)
    assert rec2.generation != rec1.generation          # rotation => revocation
    assert not dashauth.verify_password(PASSWORD_1, rec2)
    assert dashauth.verify_password(PASSWORD_2, rec2)
    # The pre-rotation session is dead under the new generation.
    assert sessions.get_valid(session.token, rec2.generation) is None

    # -- MCP key domain is UNTOUCHED by the rotation --
    from uaeg.db import Store
    from uaeg import paths

    os.environ["UAEG_DATA_DIR"] = str(env["data"])
    try:
        store = Store(paths.database_path())
        rows = store.list_keys()
        assert len(rows) == 1
        assert rows[0]["key_id"] == key_id
        assert rows[0]["name"] == "audit-key"
        store.close()
    finally:
        pass
    # The stored secret still authenticates (key material unchanged).
    from uaeg.auth import resolve_key
    from uaeg.db import Store as _Store
    from uaeg import paths as _paths

    st = _Store(_paths.database_path())
    try:
        assert resolve_key(secret, st) is not None
    finally:
        st.close()


def test_uaeg_pass_mismatch_then_success_and_length_rules(env):
    write_test_config(env)
    # mismatch -> retry loop; short -> rejected; then a valid pair.
    stdin = (
        f"{PASSWORD_1}\n{PASSWORD_2}\n"       # mismatch: retry
        "short\nshort\n"                      # too short: retry
        f"{PASSWORD_1}\n{PASSWORD_1}\n"       # accepted
    ).encode()
    proc = run_cli(env, "pass", stdin=stdin)
    assert proc.returncode == 0, proc.stderr
    assert "passwords do not match" in proc.stdout
    assert "at least" in proc.stdout
    assert "Password updated successfully" in proc.stdout


def test_password_file_untouched_by_service_restart_cycle(env):
    """The stored credential is byte-identical across a config rewrite
    (installer/config flows must never reset the password)."""
    write_test_config(env)
    run_cli(env, "pass", stdin=(f"{PASSWORD_1}\n{PASSWORD_1}\n").encode())
    before = (env["data"] / "dashboard-pass.json").read_bytes()
    # a config mutation (as the installer/repair does)
    run_cli(env, "config", "set", "dashboard.allow_public", "true")
    run_cli(env, "config", "set", "dashboard.tls", "true")
    run_cli(env, "config", "set", "dashboard.bind", "0.0.0.0")
    after = (env["data"] / "dashboard-pass.json").read_bytes()
    assert before == after


def test_database_quick_check_after_auth_cycle(env):
    write_test_config(env)
    run_cli(env, "pass", stdin=(f"{PASSWORD_1}\n{PASSWORD_1}\n").encode())
    make_key(env, "k1")
    con = sqlite3.connect(str(env["data"] / "uaeg.db"))
    try:
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM keys").fetchone()[0] == 1
    finally:
        con.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
