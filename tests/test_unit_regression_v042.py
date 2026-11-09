"""Regression tests for the 2026-11-10 live bridge audit (v0.4.2 patch set).

Findings covered (see report/UAEG_LIVE_FINDINGS_AND_FIXES.md):

  F1  MCP tools failed OPEN to operator-level job/session visibility when the
      calling agent key could not be resolved (owner_key_id=None). The tool
      layer now denies such calls (fail closed).
  F2  ``uaeg doctor`` displayed the loopback probe address (127.0.0.1) for
      wildcard-bound MCP listeners, hiding that the root-execution endpoint
      is publicly bound. It now shows the configured bind + a warning.
  F4  updater.sh / install.sh had no protection when started from INSIDE the
      UAEG service cgroup: ``systemctl stop uaeg`` (KillMode=control-group)
      would kill the updater mid-upgrade. Mutating modes now re-exec into a
      transient systemd scope (or refuse loudly).
  F5  Background jobs died together with the service under systemd despite
      the documented "jobs survive service restarts" contract: setsid does
      NOT escape the unit cgroup. Jobs are now spawned in transient systemd
      scopes when the host supports them (probe + fallback).
  F6  Every MCP client disconnect logged an ERROR traceback
      (mcp.server.streamable_http "Error in standalone SSE writer") -
      journal noise that hid real errors. Now filtered narrowly.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


# ======================================================================
# helpers
# ======================================================================
def _build_runtime(env):
    from uaeg.config import Config
    from uaeg.db import Store
    from uaeg.runtime import Runtime
    from uaeg import paths

    cfg = Config()
    cfg.public_host = "localhost"
    cfg.mcp.bind = "127.0.0.1"
    cfg.dashboard.bind = "127.0.0.1"
    cfg.tls.mode = "self-signed"
    store = Store(paths.database_path())
    return Runtime(cfg, store), store


def _tool(mcp, name):
    return mcp._tool_manager._tools[name].fn


def _register(runtime):
    from mcp.server.fastmcp import FastMCP
    from uaeg.mcp_tools import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, runtime)
    return mcp


# ======================================================================
# F1 — fail closed when the calling agent key cannot be resolved
# ======================================================================
async def test_f1_shell_exec_denies_unresolvable_identity(env):
    runtime, store = _build_runtime(env)
    try:
        fn = _tool(_register(runtime), "shell_exec")
        res = await fn(command="echo should-not-run", ctx=None)
        assert res["ok"] is False
        assert "could not be resolved" in res["error"]
        # the command must NOT have executed: no successful/failed execution
        # row exists for it. (A status="denied" audit row IS expected - the
        # denial itself is part of the audit trail.)
        rows = store.list_logs(limit=10)
        exec_rows = [
            r for r in rows
            if r.get("tool") == "shell_exec" and r.get("status") in ("ok", "error", "timeout")
            and "should-not-run" in (r.get("meta") or {}).get("command", "")
        ]
        assert exec_rows == [], "denied command must not have executed"
        denied_rows = [
            r for r in rows
            if r.get("tool") == "shell_exec" and r.get("status") == "denied"
            and "should-not-run" in (r.get("meta") or {}).get("command", "")
        ]
        assert denied_rows, "the denial itself must be audited"
    finally:
        store.close()


async def test_f1_job_and_session_tools_deny_unresolvable_identity(env):
    runtime, store = _build_runtime(env)
    try:
        mcp = _register(runtime)
        for name, kwargs in (
            ("job_status", {"job_id": "job-doesnotmatter"}),
            ("job_output", {"job_id": "job-doesnotmatter"}),
            ("job_wait", {"job_id": "job-doesnotmatter"}),
            ("job_cancel", {"job_id": "job-doesnotmatter"}),
            ("job_list", {}),
            ("shell_sessions", {}),
            ("shell_input", {"session_id": "s", "data": "x"}),
            ("shell_output", {"session_id": "s"}),
            ("shell_resize", {"session_id": "s", "cols": 80, "rows": 24}),
            ("shell_close", {"session_id": "s"}),
        ):
            res = await _tool(mcp, name)(ctx=None, **kwargs)
            assert res.get("ok") is False, (name, res)
            assert "could not be resolved" in res.get("error", ""), (name, res)
    finally:
        store.close()


async def test_f1_background_spawn_denies_unresolvable_identity(env):
    runtime, store = _build_runtime(env)
    try:
        fn = _tool(_register(runtime), "shell_exec")
        res = await fn(command="echo bg", background=True, ctx=None)
        assert res["ok"] is False
        assert "could not be resolved" in res["error"]
        assert store.list_jobs(limit=10) == [], "no job row may be created"
    finally:
        store.close()


async def test_f1_documented_operator_level_none_still_exists(env):
    """The MANAGERS keep owner_key_id=None as operator level (dashboard/CLI).

    That is intentional and is exactly why the MCP tool layer must never
    forward an unresolved identity as None (the tool-layer deny of F1 is
    the boundary, not the manager).
    """
    runtime, store = _build_runtime(env)
    try:
        job = runtime.jobs.spawn("echo x", key_id="key-a", key_name="A")
        # operator (dashboard/CLI) passes None and sees everything:
        assert runtime.jobs.status(job["job_id"], owner_key_id=None) is not None
        # another key must NOT see it:
        from uaeg.executor.jobs import JobError

        with pytest.raises(JobError):
            runtime.jobs.status(job["job_id"], owner_key_id="key-b")
    finally:
        store.close()


# ======================================================================
# F5 — transient systemd scopes for background jobs
# ======================================================================
def _write_fake_systemd_run(path: Path, record_file: Path, fail: bool = False):
    """A fake systemd-run: records its args, then execs the real command."""
    body = textwrap.dedent(
        f"""\
        #!/bin/sh
        printf '%s\\n' "$*" >> {record_file}
        if [ "{"fail" if fail else "ok"}" = "fail" ]; then
          echo "fake systemd-run: refusing" >&2
          exit 1
        fi
        while [ "$#" -gt 0 ]; do
          case "$1" in
            -*) shift ;;
            *) break ;;
          esac
        done
        exec "$@"
        """
    )
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_f5_in_systemd_service_detection(tmp_path, monkeypatch):
    from uaeg.executor import jobs as jobs_mod

    inside = tmp_path / "inside.cgroup"
    inside.write_text("0::/system.slice/uaeg.service\n")
    outside = tmp_path / "outside.cgroup"
    outside.write_text("0::/system.slice/uaeg-job-abc.scope\n")
    legacy = tmp_path / "legacy.cgroup"
    legacy.write_text("12:devices:/user.slice\n")

    monkeypatch.setattr(jobs_mod, "_CGROUP_FILE", str(inside))
    assert jobs_mod._in_systemd_service() is True
    monkeypatch.setattr(jobs_mod, "_CGROUP_FILE", str(outside))
    assert jobs_mod._in_systemd_service() is False  # scope != service
    monkeypatch.setattr(jobs_mod, "_CGROUP_FILE", str(legacy))
    assert jobs_mod._in_systemd_service() is False
    monkeypatch.setattr(jobs_mod, "_CGROUP_FILE", str(tmp_path / "missing"))
    assert jobs_mod._in_systemd_service() is False


async def test_f5_scope_supported_requires_service_cgroup(tmp_path, monkeypatch, env):
    from uaeg.executor import jobs as jobs_mod
    from uaeg.db import Store
    from uaeg import paths

    outside = tmp_path / "outside.cgroup"
    outside.write_text("0::/system.slice/other.scope\n")
    monkeypatch.setattr(jobs_mod, "_CGROUP_FILE", str(outside))
    fake = tmp_path / "systemd-run"
    _write_fake_systemd_run(fake, tmp_path / "args.log")
    monkeypatch.setattr(jobs_mod, "_SYSTEMD_RUN", str(fake))

    store = Store(paths.database_path())
    try:
        jm = jobs_mod.JobManager(store)
        assert jm._scope_supported() is False  # not inside a service cgroup
    finally:
        store.close()


async def test_f5_probe_failure_disables_scopes(tmp_path, monkeypatch, env):
    from uaeg.executor import jobs as jobs_mod
    from uaeg.db import Store
    from uaeg import paths

    inside = tmp_path / "inside.cgroup"
    inside.write_text("0::/system.slice/uaeg.service\n")
    monkeypatch.setattr(jobs_mod, "_CGROUP_FILE", str(inside))
    fake = tmp_path / "systemd-run"
    _write_fake_systemd_run(fake, tmp_path / "args.log", fail=True)
    monkeypatch.setattr(jobs_mod, "_SYSTEMD_RUN", str(fake))

    store = Store(paths.database_path())
    try:
        jm = jobs_mod.JobManager(store)
        assert jm._scope_supported() is False
        # and a job still spawns (legacy direct path):
        job = jm.spawn("echo legacy-fallback", key_id="k", key_name="n")
        await asyncio.sleep(1.5)
        st = jm.status(job["job_id"])
        assert st["status"] == "completed", st
    finally:
        store.close()


async def test_f5_jobs_spawn_in_scope_and_complete(tmp_path, monkeypatch, env):
    """Full functional path: scope probe OK -> job spawned via systemd-run
    (fake execs the real shim) -> output + exit code recorded correctly."""
    from uaeg.executor import jobs as jobs_mod
    from uaeg.db import Store
    from uaeg import paths

    inside = tmp_path / "inside.cgroup"
    inside.write_text("0::/system.slice/uaeg.service\n")
    monkeypatch.setattr(jobs_mod, "_CGROUP_FILE", str(inside))
    record = tmp_path / "args.log"
    fake = tmp_path / "systemd-run"
    _write_fake_systemd_run(fake, record)
    monkeypatch.setattr(jobs_mod, "_SYSTEMD_RUN", str(fake))

    store = Store(paths.database_path())
    try:
        jm = jobs_mod.JobManager(store)
        assert jm._scope_supported() is True
        job = jm.spawn("echo scope-job-ok", key_id="k", key_name="n")
        job_id = job["job_id"]
        # the job really ran and produced honest output:
        for _ in range(40):
            st = jm.status(job_id)
            if st["status"] in jobs_mod.FINAL_STATES:
                break
            await asyncio.sleep(0.25)
        assert st["status"] == "completed", st
        out = jm.output(job_id)
        assert out["data"].strip() == "scope-job-ok"
        assert out["exit_code"] == 0
        # the probe AND the job spawn both went through the fake (assert
        # AFTER completion: the fake's append races with spawn()'s return):
        args_text = record.read_text()
        assert "--scope" in args_text
        assert f"--unit=uaeg-{job_id}" in args_text, args_text
    finally:
        store.close()


# ======================================================================
# F2 — doctor shows the real MCP bind
# ======================================================================
def test_f2_doctor_shows_wildcard_mcp_bind(env, monkeypatch, capsys):
    from uaeg import cli
    from uaeg.config import Config
    from uaeg.db import Store
    from uaeg import paths
    from tests.conftest import free_port, write_test_config

    write_test_config(env, **{"mcp.bind": "0.0.0.0"})
    store = Store(paths.database_path())
    store.close()
    import json as _json

    mcp_port = _json.loads(env["config"].read_text())["mcp"]["port"]

    monkeypatch.setattr(cli, "_service_state", lambda: "active")
    monkeypatch.setattr(cli, "_port_listening", lambda host, port: True)
    monkeypatch.setattr(cli, "_http_get_json", lambda url, **kw: {"status": "ok"})

    rc = cli.cmd_doctor(argparse.Namespace())
    out = capsys.readouterr().out
    # the CONFIGURED bind is displayed, not the loopback probe address:
    assert f"0.0.0.0:{mcp_port}" in out
    assert "probed via 127.0.0.1" in out
    assert "ALL interfaces" in out
    assert rc in (0, 1)  # other checks (missing cert etc.) may fail in tmp env


def test_f2_doctor_shows_private_mcp_bind(env, monkeypatch, capsys):
    from uaeg import cli
    from uaeg.db import Store
    from uaeg import paths
    from tests.conftest import write_test_config

    write_test_config(env)  # 127.0.0.1 default from helper
    store = Store(paths.database_path())
    store.close()

    monkeypatch.setattr(cli, "_service_state", lambda: "active")
    monkeypatch.setattr(cli, "_port_listening", lambda host, port: True)
    monkeypatch.setattr(cli, "_http_get_json", lambda url, **kw: {"status": "ok"})

    cli.cmd_doctor(argparse.Namespace())
    out = capsys.readouterr().out
    assert "probed via" not in out  # private bind displayed as-is
    assert "127.0.0.1:" in out


# ======================================================================
# F4 — self-update guard in updater.sh / install.sh (extracted verbatim)
# ======================================================================
def _extract_guard(script: Path) -> str:
    out = subprocess.run(
        ["sed", "-n", "/^selfupdate_guard() {/,/^}/p", str(script)],
        capture_output=True, text=True, check=True,
    )
    assert "selfupdate_guard() {" in out.stdout and out.stdout.rstrip().endswith("}")
    return out.stdout


def _guard_harness(script: Path, workdir: Path, mode_env: dict) -> str:
    """Build a runnable harness around the extracted guard function."""
    harness = textwrap.dedent(
        f"""\
        #!/bin/bash
        MODE="upgrade"
        MODE_CMD=""
        NO_SERVICE=0
        SERVICE_NAME="uaeg"
        UPDATER_SELF="{script}"
        SELF_FILE="{script}"
        say() {{ printf '==> %s\\n' "$*"; }}
        die() {{ printf 'FATAL: %s\\n' "$*" >&2; exit 9; }}
        {_extract_guard(script)}
        selfupdate_guard "$@"
        echo GUARD-PASSED-THROUGH
        """
    )
    for k, v in mode_env.items():
        harness = harness.replace(f'{k}="upgrade"' if k == "MODE" else f'{k}=""', f'{k}="{v}"', 1)
    path = workdir / "harness.sh"
    path.write_text(harness)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_f4_updater_guard_reexecs_into_scope(tmp_path):
    """Inside the uaeg.service cgroup + systemd-run available + real file ->
    the guard must exec systemd-run (the fake exits 42 and records args)."""
    script = tmp_path / "updater.sh"
    script.write_text(REPO.joinpath("updater.sh").read_text())
    cg = tmp_path / "cg"
    cg.write_text("0::/system.slice/uaeg.service\n")
    record = tmp_path / "args.log"
    fake_sd = tmp_path / "systemd-run"
    fake_sd.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> {record}\nexit 42\n'
    )
    fake_sd.chmod(fake_sd.stat().st_mode | stat.S_IXUSR)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    os.symlink(fake_sd, bindir / "systemd-run")

    harness = _guard_harness(script, tmp_path, {"MODE": "upgrade"})
    proc = subprocess.run(
        ["bash", harness, "--yes"],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
             "UAEG_CGROUP_FILE": str(cg)},
        timeout=30,
    )
    assert proc.returncode == 42, proc.stderr  # the fake systemd-run ran
    args = record.read_text()
    assert "--scope" in args and "--unit=uaeg-selfupdate-" in args
    assert str(script) in args and "--yes" in args
    assert "GUARD-PASSED-THROUGH" not in proc.stdout  # exec replaced the flow


def test_f4_updater_guard_refuses_without_systemd_run(tmp_path):
    script = tmp_path / "updater.sh"
    script.write_text(REPO.joinpath("updater.sh").read_text())
    cg = tmp_path / "cg"
    cg.write_text("0::/system.slice/uaeg.service\n")
    # A PATH with ONLY the essentials: no systemd-run anywhere reachable.
    minimal_bin = tmp_path / "minbin"
    minimal_bin.mkdir()
    for tool in ("cat", "readlink", "dirname", "pwd"):
        try:
            os.symlink(f"/usr/bin/{tool}", minimal_bin / tool)
        except FileExistsError:
            pass

    harness = _guard_harness(script, tmp_path, {"MODE": "upgrade"})
    proc = subprocess.run(
        ["/bin/bash", harness],
        capture_output=True, text=True,
        env={"PATH": str(minimal_bin), "UAEG_CGROUP_FILE": str(cg), "HOME": str(tmp_path)},
        timeout=30,
    )
    assert proc.returncode == 9
    assert "refusing to run a mutating upgrade" in proc.stderr


def test_f4_updater_guard_skips_readonly_modes(tmp_path):
    script = tmp_path / "updater.sh"
    script.write_text(REPO.joinpath("updater.sh").read_text())
    cg = tmp_path / "cg"
    cg.write_text("0::/system.slice/uaeg.service\n")

    harness = _guard_harness(script, tmp_path, {"MODE": "check"})
    proc = subprocess.run(
        ["bash", harness],
        capture_output=True, text=True,
        env={**os.environ, "UAEG_CGROUP_FILE": str(cg)},
        timeout=30,
    )
    assert proc.returncode == 0
    assert "GUARD-PASSED-THROUGH" in proc.stdout


def test_f4_updater_guard_noop_outside_service_cgroup(tmp_path):
    script = tmp_path / "updater.sh"
    script.write_text(REPO.joinpath("updater.sh").read_text())
    cg = tmp_path / "cg"
    cg.write_text("0::/system.slice/uaeg-job-123.scope\n")

    harness = _guard_harness(script, tmp_path, {"MODE": "upgrade"})
    proc = subprocess.run(
        ["bash", harness],
        capture_output=True, text=True,
        env={**os.environ, "UAEG_CGROUP_FILE": str(cg)},
        timeout=30,
    )
    assert proc.returncode == 0
    assert "GUARD-PASSED-THROUGH" in proc.stdout


def test_f4_install_guard_modes(tmp_path):
    """install.sh guard: mutating modes re-exec; read-only modes pass."""
    script = tmp_path / "install.sh"
    script.write_text(REPO.joinpath("install.sh").read_text())
    cg = tmp_path / "cg"
    cg.write_text("0::/system.slice/uaeg.service\n")
    record = tmp_path / "args.log"
    fake_sd = tmp_path / "systemd-run"
    fake_sd.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> {record}\nexit 42\n')
    fake_sd.chmod(fake_sd.stat().st_mode | stat.S_IXUSR)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    os.symlink(fake_sd, bindir / "systemd-run")

    # default MODE_CMD="" (install/upgrade/repair flow) -> re-exec
    harness = _guard_harness(script, tmp_path, {})
    proc = subprocess.run(
        ["bash", harness],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
             "UAEG_CGROUP_FILE": str(cg)},
        timeout=30,
    )
    assert proc.returncode == 42
    assert "--scope" in record.read_text()

    # read-only status mode -> passes through
    harness_ro = _guard_harness(script, tmp_path, {"MODE_CMD": "status"})
    proc_ro = subprocess.run(
        ["bash", harness_ro],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
             "UAEG_CGROUP_FILE": str(cg)},
        timeout=30,
    )
    assert proc_ro.returncode == 0
    assert "GUARD-PASSED-THROUGH" in proc_ro.stdout


def test_f4_shell_scripts_remain_syntax_valid():
    for name in ("install.sh", "updater.sh", "upgrade.sh", "uninstall.sh"):
        proc = subprocess.run(["bash", "-n", str(REPO / name)], capture_output=True, text=True)
        assert proc.returncode == 0, (name, proc.stderr)


# ======================================================================
# F6 — MCP client-disconnect noise filter
# ======================================================================
def test_f6_disconnect_noise_filtered():
    from uaeg.logging_util import _McpDisconnectNoiseFilter

    f = _McpDisconnectNoiseFilter()

    def record(msg, exc=None):
        try:
            if exc is not None:
                raise exc
        except Exception as e:  # noqa: BLE001
            exc_info = sys.exc_info()
        else:
            exc_info = None
        return logging.LogRecord(
            "mcp.server.streamable_http", logging.ERROR, __file__, 1, msg, None, exc_info
        )

    import anyio

    # the known benign disconnect traceback is dropped:
    assert f.filter(record("Error in standalone SSE writer", anyio.ClosedResourceError())) is False
    # a different error in that writer still logs:
    assert f.filter(record("Error in standalone SSE writer", RuntimeError("boom"))) is True
    # unrelated messages are untouched:
    assert f.filter(record("some other error", anyio.ClosedResourceError())) is True
    # CRITICAL records are never dropped:
    crit = logging.LogRecord(
        "mcp.server.streamable_http", logging.CRITICAL, __file__, 1,
        "Error in standalone SSE writer", None, None,
    )
    assert f.filter(crit) is True


def test_f6_filter_installed_by_setup_logging():
    import logging as _logging

    from uaeg.logging_util import setup_logging, _McpDisconnectNoiseFilter

    setup_logging()
    logger = _logging.getLogger("mcp.server.streamable_http")
    assert any(isinstance(flt, _McpDisconnectNoiseFilter) for flt in logger.filters)
