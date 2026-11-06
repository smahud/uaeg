"""UAEG command-line interface (PRD FR-011, FR-012, FR-020).

Subcommands:
    uaeg serve                       run the server in the foreground
    uaeg init                        write a default configuration file
    uaeg generate [--name N]         create an agent key (secret shown once)
    uaeg keys list|revoke|rotate     key administration
    uaeg jobs list|status|output|cancel
    uaeg sessions list|kill
    uaeg status                      service + endpoint status
    uaeg doctor                      full diagnostics
    uaeg config show|get|set         configuration management
    uaeg version

All commands honor UAEG_CONFIG / UAEG_DATA_DIR environment overrides.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any

from . import __version__, paths, security
from .config import Config, ConfigError, PERMISSION_MODES

OK = "\u2713"   # check mark
FAIL = "\u2717"  # cross mark
WARN = "!"


def _load_config() -> Config:
    return Config.load()


def _store():
    from .db import Store

    return Store(paths.database_path())


# ----------------------------------------------------------------------
# serve / init
# ----------------------------------------------------------------------
def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    return serve()


def cmd_init(args: argparse.Namespace) -> int:
    path = paths.config_path()
    if path.exists() and not getattr(args, "force", False):
        print(f"config already exists: {path} (use --force to overwrite)")
        return 1
    cfg = Config()
    cfg.public_host = getattr(args, "host", None) or cfg.public_host
    cfg.save(path)
    print(f"wrote default configuration: {path}")
    print("edit it (dashboard bind, TLS mode, ports) and then run: uaeg doctor")
    return 0


# ----------------------------------------------------------------------
# generate / keys
# ----------------------------------------------------------------------
def cmd_generate(args: argparse.Namespace) -> int:
    store = _store()
    material = security.new_key(args.name)
    store.create_key(
        key_id=material.key_id,
        name=material.name,
        prefix=material.prefix,
        verifier_hex=material.verifier,
        permission_mode=args.permissions,
    )
    try:
        cfg = _load_config()
        url = cfg.mcp_url()
    except ConfigError:
        url = "https://<HOST>:3902/mcp (config not found - run uaeg init or install.sh)"
    print(f"Agent key created: {material.name}  ({material.key_id})")
    print()
    print(f"  Secret (shown ONCE, store it now):")
    print(f"    {material.secret}")
    print()
    print(f"  MCP endpoint : {url}")
    print(f"  Header       : Authorization: Bearer <secret>")
    print(f"  Permissions  : {args.permissions}")
    return 0


def cmd_keys(args: argparse.Namespace) -> int:
    store = _store()
    action = args.keys_action
    if action == "list":
        keys = store.list_keys()
        if not keys:
            print("no keys yet - create one with: uaeg generate --name \"Claude\"")
            return 0
        print(f"{'KEY ID':<16} {'NAME':<18} {'PREFIX':<20} {'STATUS':<9} {'MODE':<11} {'CREATED (UTC)':<24} {'LAST USED'}")
        for k in keys:
            print(
                f"{k['key_id']:<16} {(k['name'] or '')[:18]:<18} {k['prefix']+'...':<20} "
                f"{k['status']:<9} {k['permission_mode']:<11} "
                f"{(k['created_at'] or '')[:19]:<24} {(k['last_used_at'] or 'never')[:19]}"
            )
        return 0
    if action == "revoke":
        key = store.revoke_key(args.key_id)
        if not key:
            print(f"error: unknown key_id or already revoked: {args.key_id}")
            return 1
        print(f"revoked {key['key_id']} ({key['name']}) - it can no longer authenticate")
        return 0
    if action == "rotate":
        existing = store.get_key(args.key_id)
        if not existing:
            print(f"error: unknown key_id: {args.key_id}")
            return 1
        name = getattr(args, "name", None) or existing["name"]
        material = security.new_key(name)
        store.revoke_key(args.key_id)
        store.create_key(
            key_id=material.key_id,
            name=material.name,
            prefix=material.prefix,
            verifier_hex=material.verifier,
            permission_mode=existing.get("permission_mode", "all"),
        )
        print(f"rotated {args.key_id} -> {material.key_id} ({material.name})")
        print(f"  new secret (shown ONCE): {material.secret}")
        return 0
    print(f"error: unknown keys action: {action}")
    return 2


# ----------------------------------------------------------------------
# jobs / sessions
# ----------------------------------------------------------------------
def cmd_jobs(args: argparse.Namespace) -> int:
    from .executor.jobs import JobError

    store = _store()
    action = args.jobs_action
    if action == "list":
        jobs = store.list_jobs(limit=args.limit)
        if not jobs:
            print("no jobs recorded")
            return 0
        print(f"{'JOB ID':<38} {'STATUS':<12} {'EXIT':<6} {'STARTED (UTC)':<24} COMMAND")
        for j in jobs:
            print(
                f"{j['job_id']:<38} {j['status']:<12} {str(j['exit_code'] if j['exit_code'] is not None else '-'):<6} "
                f"{(j['started_at'] or '')[:19]:<24} {(j['command'] or '')[:60]}"
            )
        return 0
    if action == "status":
        job = store.get_job(args.job_id)
        if not job:
            print(f"error: unknown job_id: {args.job_id}")
            return 1
        for k, v in job.items():
            print(f"  {k:<12} {v}")
        return 0
    if action == "output":
        # Read output files directly (operator-side inspection).
        path = paths.jobs_dir() / args.job_id / args.stream
        if not path.exists():
            print(f"no {args.stream} recorded for job {args.job_id}")
            return 1
        data = path.read_bytes()
        if args.offset:
            data = data[args.offset:]
        if args.limit:
            data = data[: args.limit]
        sys.stdout.write(data.decode("utf-8", errors="replace"))
        if data and not data.endswith(b"\n"):
            print()
        return 0
    if action == "cancel":
        print("note: job cancellation runs inside the server process.")
        print("use the dashboard (Sessions tab) or the MCP job_cancel tool, or:")
        print(f"  kill -- -$(cat {paths.jobs_dir()}/{args.job_id}/pid 2>/dev/null)")
        return 1
    print(f"error: unknown jobs action: {action}")
    return 2


def cmd_sessions(args: argparse.Namespace) -> int:
    store = _store()
    if args.sessions_action == "list":
        sessions = store.list_sessions(limit=args.limit)
        if not sessions:
            print("no sessions recorded")
            return 0
        print(f"{'SESSION ID':<34} {'STATUS':<12} {'KEY':<14} {'CREATED (UTC)':<24} COMMAND")
        for s in sessions:
            print(
                f"{s['session_id']:<34} {s['status']:<12} {(s['key_name'] or '-')[:14]:<14} "
                f"{(s['created_at'] or '')[:19]:<24} {(s['command'] or '')[:50]}"
            )
        return 0
    if args.sessions_action == "kill":
        print("note: live sessions live inside the server process.")
        print("terminate via the dashboard (Sessions tab) or the shell_close MCP tool.")
        return 1
    return 2


# ----------------------------------------------------------------------
# status / doctor
# ----------------------------------------------------------------------
def _service_state() -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["systemctl", "is-active", "uaeg"], capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 - no systemd / not installed
        return "unavailable (no systemd or not installed as a service)"


def _http_get_json(url: str, insecure_tls: bool = True, timeout: float = 4.0) -> dict | None:
    ctx = ssl._create_unverified_context() if insecure_tls else None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"uaeg/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _port_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def cmd_status(args: argparse.Namespace) -> int:
    try:
        cfg = _load_config()
    except ConfigError as exc:
        print(f"error: {exc}")
        return 2

    print(f"UAEG v{__version__}")
    print(f"  service      : {_service_state()}")
    bind = cfg.mcp.bind if cfg.mcp.bind not in ("0.0.0.0", "::") else "127.0.0.1"
    listening = _port_listening(bind, cfg.mcp.port)
    print(f"  MCP port     : {cfg.mcp.port} ({'listening' if listening else 'NOT listening'})")
    dash_listening = _port_listening(cfg.dashboard.bind, cfg.dashboard.port)
    print(
        f"  dashboard    : {cfg.dashboard.bind}:{cfg.dashboard.port} "
        f"({'listening' if dash_listening else 'NOT listening'})"
    )
    health = _http_get_json(f"https://{bind}:{cfg.mcp.port}/healthz")
    if health:
        print(
            f"  health       : {health.get('status')} (v{health.get('version')}, "
            f"uptime {round(health.get('uptime_seconds', 0))}s)"
        )
    else:
        print("  health       : unreachable")
    if paths.database_path().exists():
        store = _store()
        s = store.summary()
        print(
            f"  keys         : {s['keys_active']} active, {s['keys_revoked']} revoked"
        )
        print(
            f"  jobs/sessions: {s['jobs_running']} running jobs, {s['sessions_open']} open sessions"
        )
        store.close()
    else:
        print(f"  database     : not initialized ({paths.database_path()})")
    print(f"  MCP URL      : {cfg.mcp_url()}")
    return 0 if (listening and health) else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        mark = OK if ok else FAIL
        line = f"  [{mark}] {name}"
        if detail:
            line += f": {detail}"
        print(line)

    print(f"UAEG doctor v{__version__}")
    print("  host:", os.uname().nodename, "| user:", os.environ.get("USER") or os.getuid())

    # config
    try:
        cfg = _load_config()
        check("config", True, str(paths.config_path()))
        warnings = cfg.validate()
        for w in warnings:
            print(f"        ! {w}")
    except ConfigError as exc:
        check("config", False, str(exc).splitlines()[0])
        print("\nfix the configuration (uaeg config set / reinstall) and re-run uaeg doctor")
        return 1

    # data dir & database
    check("data dir", paths.data_dir().is_dir(), str(paths.data_dir()))
    db_ok = False
    if paths.database_path().exists():
        try:
            store = _store()
            s = store.summary()
            db_ok = True
            check("database", True,
                  f"{s['keys_active']} active keys, {s['log_entries']} log entries")
            store.close()
        except Exception as exc:  # noqa: BLE001
            check("database", False, str(exc))
    else:
        check("database", False, f"not initialized ({paths.database_path()})")

    # service & listeners
    state = _service_state()
    check("systemd service", state == "active", state)
    bind = cfg.mcp.bind if cfg.mcp.bind not in ("0.0.0.0", "::") else "127.0.0.1"
    check("MCP listener", _port_listening(bind, cfg.mcp.port), f"{bind}:{cfg.mcp.port}")
    check("dashboard listener", _port_listening(cfg.dashboard.bind, cfg.dashboard.port),
          f"{cfg.dashboard.bind}:{cfg.dashboard.port}")

    health = _http_get_json(f"https://{bind}:{cfg.mcp.port}/healthz")
    check("health endpoint", bool(health),
          f"status={health.get('status') if health else 'unreachable'}")

    # TLS certificate
    cert_path = None
    if cfg.tls.mode == "manual" and cfg.tls.cert:
        cert_path = cfg.tls.cert
    elif cfg.tls.mode == "certbot":
        cert_path = f"/etc/letsencrypt/live/{cfg.public_host}/fullchain.pem"
    else:
        cert_path = str(paths.tls_dir() / "self-signed.crt")
    tls_detail = f"mode={cfg.tls.mode}"
    if cert_path and os.path.isfile(cert_path):
        import subprocess

        out = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-subject", "-enddate", "-checkend", "2592000"],
            capture_output=True, text=True, timeout=10,
        )
        subject = ""
        expires = ""
        for line in out.stdout.splitlines():
            if line.startswith("subject="):
                subject = line.split("=", 1)[1]
            elif line.startswith("notAfter="):
                expires = line.split("=", 1)[1]
        expiring = out.returncode != 0  # checkend: rc!=0 => expires within 30d
        check("TLS certificate", True, f"{tls_detail}; {subject}; expires {expires}")
        if cfg.tls.mode == "self-signed":
            print("        ! self-signed: hosted MCP clients will NOT trust this certificate")
        if expiring:
            print("        ! certificate expires within 30 days - renew it")
    else:
        check("TLS certificate", False, f"missing: {cert_path}")

    # execution context
    if os.geteuid() == 0:
        check("execution user", True, "root (full OS control per PRD FR-010)")
    else:
        check("execution user", False,
              f"not root (uid={os.geteuid()}); agents will NOT get root execution - "
              "run the service as root for the intended behavior")

    shell = cfg.exec.shell
    check("exec shell", os.path.exists(shell), shell)

    if db_ok and cfg.tls.mode == "self-signed":
        print()
        print("  summary: functional, but agent connections from hosted platforms will")
        print("  fail TLS validation. Install with a domain (certbot) or trusted certs.")
    failed = [c for c in checks if not c[1]]
    print()
    if failed:
        print(f"  {len(failed)} check(s) failed" + ("" if db_ok else " - see above"))
        return 1
    print("  all checks passed")
    return 0


# ----------------------------------------------------------------------
# config
# ----------------------------------------------------------------------
def cmd_config(args: argparse.Namespace) -> int:
    try:
        cfg = _load_config()
    except ConfigError as exc:
        print(f"error: {exc}")
        return 2

    action = args.config_action
    if action == "show":
        print(json.dumps(cfg.to_dict(), indent=2))
        return 0
    if action == "get":
        value = _dig(cfg.to_dict(), args.key)
        if value is None and _dig(cfg.to_dict(), args.key, missing_ok=True) is None:
            print(f"error: unknown config key: {args.key}")
            return 1
        print(json.dumps(value) if isinstance(value, (dict, list)) else value)
        return 0
    if action == "set":
        data = cfg.to_dict()
        if not _set_dotted(data, args.key, _coerce(args.value)):
            print(f"error: unknown config key: {args.key}")
            return 1
        try:
            new_cfg = Config.from_dict(data)
            warnings = new_cfg.validate()
        except ConfigError as exc:
            print(f"error: {exc}")
            return 1
        new_cfg.save()
        for w in warnings:
            print(f"warning: {w}")
        print(f"saved {args.key} = {args.value}")
        print("note: restart the service to apply: sudo systemctl restart uaeg")
        return 0
    return 2


def _dig(data: dict, dotted: str, missing_ok: bool = False):
    node: Any = data
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            if missing_ok:
                return None
            return None
    return node


def _set_dotted(data: dict, dotted: str, value: Any) -> bool:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    last = parts[-1]
    if not isinstance(node, dict) or last not in node:
        return False
    node[last] = value
    return True


def _coerce(raw: str) -> Any:
    low = raw.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if raw.startswith("[") or raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return raw


# ----------------------------------------------------------------------
# parser
# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uaeg",
        description="UAEG - Universal Agent Execution Gateway (MCP bridge to native VPS execution)",
    )
    parser.add_argument("--version", action="version", version=f"uaeg {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the server in the foreground (used by systemd)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("init", help="write a default configuration file")
    p.add_argument("--host", help="public host or IP for the default config")
    p.add_argument("--force", action="store_true", help="overwrite an existing config")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("generate", help="generate a new agent API key")
    p.add_argument("--name", default="Unnamed agent", help="display name for the key")
    p.add_argument("--permissions", choices=PERMISSION_MODES, default="all",
                   help="permission mode (default: all)")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("keys", help="key administration")
    keys_sub = p.add_subparsers(dest="keys_action", required=True)
    keys_sub.add_parser("list", help="list all keys")
    pr = keys_sub.add_parser("revoke", help="revoke a key immediately")
    pr.add_argument("key_id")
    pr = keys_sub.add_parser("rotate", help="revoke a key and issue a replacement")
    pr.add_argument("key_id")
    pr.add_argument("--name", help="new display name (default: keep current)")
    p.set_defaults(func=cmd_keys)

    p = sub.add_parser("jobs", help="background job inspection")
    jobs_sub = p.add_subparsers(dest="jobs_action", required=True)
    pj = jobs_sub.add_parser("list", help="list recent jobs")
    pj.add_argument("--limit", type=int, default=50)
    pj = jobs_sub.add_parser("status", help="show job metadata")
    pj.add_argument("job_id")
    pj = jobs_sub.add_parser("output", help="print captured job output")
    pj.add_argument("job_id")
    pj.add_argument("--stream", choices=["stdout", "stderr"], default="stdout")
    pj.add_argument("--offset", type=int, default=0)
    pj.add_argument("--limit", type=int, default=0, help="max bytes (0 = all)")
    jobs_sub.add_parser("cancel", help="explain how to cancel a job")
    p.set_defaults(func=cmd_jobs)

    p = sub.add_parser("sessions", help="terminal session inspection")
    s_sub = p.add_subparsers(dest="sessions_action", required=True)
    ps = s_sub.add_parser("list", help="list recent sessions")
    ps.add_argument("--limit", type=int, default=100)
    s_sub.add_parser("kill", help="explain how to terminate a session")
    p.set_defaults(func=cmd_sessions)

    sub.add_parser("status", help="service and endpoint status").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="run full diagnostics").set_defaults(func=cmd_doctor)

    def _cmd_version(args: argparse.Namespace) -> int:
        print(f"uaeg {__version__}")
        return 0

    sub.add_parser("version", help="print the UAEG version").set_defaults(func=_cmd_version)

    p = sub.add_parser("config", help="configuration management")
    c_sub = p.add_subparsers(dest="config_action", required=True)
    c_sub.add_parser("show", help="print the effective configuration")
    pg = c_sub.add_parser("get", help="get a dotted config value")
    pg.add_argument("key")
    ps = c_sub.add_parser("set", help="set a dotted config value and save")
    ps.add_argument("key")
    ps.add_argument("value")
    p.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
