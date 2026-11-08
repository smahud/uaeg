"""Server entrypoint: ``uaeg serve``.

Runs ONE process with TWO listeners (PRD section 4, FR-018):

- MCP listener: public HTTPS (TLS required; explicit UAEG_DEV_INSECURE_HTTP=1
  escape hatch for local development only).
- Dashboard listener: private LAN/loopback by default; can be exposed
  publicly (0.0.0.0) ONLY with dashboard.tls=true AND a dashboard
  password created via ``uaeg pass`` (fail-closed startup otherwise).

Both share the same Runtime (config, store, jobs, sessions).

Shutdown semantics: SIGTERM/SIGINT stop both listeners and all watchers;
detached background jobs are intentionally left running (PRD FR-009).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sqlite3
import ssl
import subprocess
import sys

import uvicorn

from . import __version__, paths
from .app import build_mcp_asgi_app
from .config import Config, ConfigError
from .dashboard import build_dashboard_asgi_app
from .db import Store
from .logging_util import setup_logging
from .runtime import Runtime

log = logging.getLogger("uaeg.server")


class _NoSignalServer(uvicorn.Server):
    """uvicorn Server that does not install its own signal handlers, so a
    single top-level handler can drive both listeners."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


class ListenerFailed(Exception):
    """A listener could not start or died unexpectedly."""


def _resolve_tls_files(config: Config) -> tuple[str, str]:
    """Validated certificate/key paths for the configured TLS mode.

    Generates the self-signed pair on demand in self-signed mode. Raises
    ConfigError (fail closed) when the files are missing or unloadable.
    """
    cert_path, key_path = _resolve_tls_paths(config)

    if config.tls.mode == "self-signed" and (
        not os.path.isfile(cert_path) or not os.path.isfile(key_path)
    ):
        _ensure_self_signed(config)

    if not os.path.isfile(cert_path) or not os.path.isfile(key_path):
        raise ConfigError(
            f"TLS certificate files missing (mode={config.tls.mode}, cert={cert_path}). "
            "The public MCP endpoint requires HTTPS; refusing to start insecure. "
            "Re-run install.sh or fix tls.* in the config."
        )

    # Pre-validate that the pair actually loads, so a broken certificate
    # fails loudly at startup with a clear error instead of as a listener
    # crash later.
    probe = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    probe.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        probe.load_cert_chain(cert_path, key_path)
    except (ssl.SSLError, OSError) as exc:
        raise ConfigError(
            f"cannot load the TLS certificate/key (cert={cert_path}, key={key_path}): "
            f"{exc}. Provide a valid, matching certificate pair (check expiry, "
            "format and file permissions) and restart."
        ) from exc
    return cert_path, key_path


def _build_ssl_context(
    config: Config,
) -> tuple[str | None, str | None, ssl.SSLContext | None]:
    """Resolve TLS material for the public MCP listener.

    Returns ``(cert_path, key_path, ssl_ctx)``. The context is ``None`` ONLY
    when the explicit UAEG_DEV_INSECURE_HTTP=1 development escape hatch is
    set (documented dev-only; never for production - PRD FR-017).
    """
    if os.environ.get("UAEG_DEV_INSECURE_HTTP") == "1":
        log.warning(
            "UAEG_DEV_INSECURE_HTTP=1 set: serving MCP over plain HTTP "
            "(isolated local development ONLY - never expose this to a network)"
        )
        return None, None, None

    cert_path, key_path = _resolve_tls_files(config)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert_path, key_path)
    return cert_path, key_path, ctx


def _check_dashboard_public_guard(config: Config) -> None:
    """Fail-closed guard: a public dashboard bind REQUIRES a password.

    ``Config.validate()`` already enforces allow_public=true and
    dashboard.tls=true for wildcard binds; this checks the remaining piece
    (an actual dashboard password) at serve time, so the service never
    exposes an unauthenticated dashboard to the internet - not even for
    a moment.
    """
    from . import dashauth
    from .config import WILDCARD_BINDS

    if config.dashboard.bind not in WILDCARD_BINDS:
        return
    if not dashauth.password_is_configured():
        raise ConfigError(
            "the dashboard is configured to bind all interfaces (public), but "
            "no dashboard password is set. Refusing to start. Create one "
            "interactively with: sudo uaeg pass - then restart the service."
        )


def _resolve_tls_paths(config: Config) -> tuple[str | None, str | None]:
    tls = config.tls
    if tls.mode == "manual":
        return tls.cert, tls.key
    if tls.mode == "certbot":
        base = f"/etc/letsencrypt/live/{config.public_host}"
        return f"{base}/fullchain.pem", f"{base}/privkey.pem"
    # self-signed
    return str(paths.tls_dir() / "self-signed.crt"), str(paths.tls_dir() / "self-signed.key")


def _ensure_self_signed(config: Config) -> None:
    """Generate a self-signed certificate if absent (openssl CLI)."""
    cert = paths.tls_dir() / "self-signed.crt"
    key = paths.tls_dir() / "self-signed.key"
    if cert.exists() and key.exists():
        return
    paths.tls_dir().mkdir(parents=True, exist_ok=True)
    host = config.public_host
    # Add SAN for both DNS and IP forms so either works.
    san = f"subjectAltName=DNS:{host}"
    try:
        import ipaddress

        ipaddress.ip_address(host)
        san = f"subjectAltName=IP:{host}"
    except ValueError:
        pass
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "397",
        "-nodes", "-subj", f"/CN={host}", "-addext", san,
        "-keyout", str(key), "-out", str(cert),
    ]
    log.warning(
        "generating SELF-SIGNED certificate for %s - hosted MCP clients will "
        "not trust it; use a domain + certbot or manual certificates for "
        "production agent connections", host,
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ConfigError(f"openssl self-signed certificate generation failed: {proc.stderr.strip()}")


async def _amain(config: Config) -> int:
    try:
        store = Store(paths.database_path())
    except sqlite3.DatabaseError as exc:
        raise ConfigError(
            f"the UAEG database is unreadable or corrupt "
            f"({paths.database_path()}): {exc}. Recovery: stop the service, move "
            "the file aside (it can be inspected later), restart - a fresh "
            "database is created (keys must be re-issued). See "
            "docs/TROUBLESHOOTING.md."
        ) from exc
    runtime = Runtime(config, store)

    # Audit-log retention (B-11): one bounded pass at startup keeps exec_logs
    # from growing without limit on a long-lived VPS. 20k rows of redacted
    # audit history is far beyond single-operator needs.
    try:
        removed = store.trim_exec_logs(keep=20_000)
        if removed:
            log.info("audit log retention: removed %d old exec_logs row(s)", removed)
    except sqlite3.DatabaseError:
        log.exception("audit log retention failed (non-fatal)")

    # Reconcile persisted state with a possibly-restarted server.
    job_counts = runtime.jobs.reattach()
    session_count = runtime.sessions.startup_reconcile()
    log.info(
        "startup reconciliation: %s jobs, %d sessions from previous run",
        job_counts, session_count,
    )

    mcp_app = build_mcp_asgi_app(runtime)
    dashboard_app = build_dashboard_asgi_app(runtime)

    # Public-dashboard guard (fail closed): password required for 0.0.0.0.
    _check_dashboard_public_guard(config)

    tls_result = _build_ssl_context(config)
    cert_path, key_path, ssl_ctx = tls_result

    # Dashboard TLS: same certificate material as the MCP listener.
    dash_cert = dash_key = None
    if config.dashboard.tls:
        if os.environ.get("UAEG_DEV_INSECURE_HTTP") == "1":
            log.warning(
                "UAEG_DEV_INSECURE_HTTP=1 set: keeping dashboard TLS disabled "
                "for this local development run"
            )
        else:
            dash_cert, dash_key = _resolve_tls_files(config)

    mcp_server = _NoSignalServer(
        uvicorn.Config(
            mcp_app,
            host=config.mcp.bind,
            port=config.mcp.port,
            ssl_certfile=cert_path,
            ssl_keyfile=key_path,
            log_level="warning",
            access_log=False,
            # Streamable HTTP: keep long-lived requests possible
            timeout_graceful_shutdown=5,
        )
    )
    dashboard_server = _NoSignalServer(
        uvicorn.Config(
            dashboard_app,
            host=config.dashboard.bind,
            port=config.dashboard.port,
            ssl_certfile=dash_cert,
            ssl_keyfile=dash_key,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=3,
        )
    )

    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    def _request_stop() -> None:
        if not stop.done():
            stop.set_result(None)
        mcp_server.should_exit = True
        dashboard_server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)

    log.info(
        "UAEG v%s starting: MCP %s (tls=%s) | dashboard %s://%s:%d | data=%s",
        __version__,
        runtime.mcp_url(),
        "on" if ssl_ctx else "OFF(dev)",
        "https" if config.dashboard.tls else "http",
        config.dashboard.bind,
        config.dashboard.port,
        paths.data_dir(),
    )

    # Dashboard security posture: say it loudly at startup.
    from . import dashauth as _dashauth
    from .config import WILDCARD_BINDS as _WILDCARD

    if _dashauth.password_is_configured():
        log.info(
            "dashboard authentication: password login enforced "
            "(sessions expire after %ds idle / %ds absolute)",
            config.dashboard.session_idle_timeout_s,
            config.dashboard.session_absolute_timeout_s,
        )
    elif config.dashboard.bind not in _WILDCARD:
        log.warning(
            "dashboard has NO password (legacy unauthenticated mode). It is "
            "reachable only on the private bind %s. Run 'sudo uaeg pass' to "
            "enable the login.",
            config.dashboard.bind,
        )

    async def _supervised_serve(server: _NoSignalServer, name: str) -> None:
        # uvicorn calls sys.exit(1) when it cannot bind; convert that (and any
        # other failure) into a normal exception so the failure is handled by
        # supervision below instead of tearing through the event loop.
        try:
            await server.serve()
        except SystemExit as exc:
            raise ListenerFailed(f"{name} failed to start (uvicorn exit code {exc.code})") from exc

    mcp_task = asyncio.create_task(_supervised_serve(mcp_server, "MCP"), name="uaeg-mcp")
    dash_task = asyncio.create_task(
        _supervised_serve(dashboard_server, "dashboard"), name="uaeg-dashboard"
    )

    # Supervision: if EITHER listener dies for ANY reason (bind failure,
    # crash, unexpected return) while the service is supposed to run, stop
    # the other listener too and exit non-zero. A half-running service that
    # systemd considers healthy is unacceptable (P1 audit finding).
    listener_failures: list[str] = []

    def _listener_done(task: asyncio.Task) -> None:
        if stop.done():
            return  # normal signal-driven shutdown already in progress
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except BaseException as exc:  # noqa: BLE001 - supervision catches all
            detail = f"{task.get_name()} listener failed: {exc!r}"
        else:
            detail = f"{task.get_name()} listener stopped unexpectedly"
        listener_failures.append(detail)
        log.critical("%s - stopping the whole service", detail)
        _request_stop()

    mcp_task.add_done_callback(_listener_done)
    dash_task.add_done_callback(_listener_done)

    try:
        await stop
    finally:
        for task in (mcp_task, dash_task):
            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=10)
                except asyncio.TimeoutError:
                    task.cancel()
                except BaseException:  # noqa: BLE001 - retrieved below
                    pass
        # Retrieve results so failed-listener exceptions are never left
        # unreported ("Task exception was never retrieved").
        for task in (mcp_task, dash_task):
            if task.cancelled():
                continue
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except BaseException as exc:  # noqa: BLE001
                detail = f"{task.get_name()} ended with {exc!r}"
                if detail not in listener_failures:
                    listener_failures.append(detail)
        await runtime.sessions.shutdown()
        await runtime.jobs.shutdown()
        store.close()

    if listener_failures:
        for detail in listener_failures:
            log.error("listener failure: %s", detail)
        log.error(
            "UAEG stopped with a failed listener; the service does not run "
            "half-alive (systemd Restart=on-failure will retry)"
        )
        return 1
    log.info("UAEG stopped cleanly (background jobs keep running detached)")
    return 0


def serve() -> int:
    setup_logging()
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    warnings = config.validate()
    for w in warnings:
        log.warning("config: %s", w)
    try:
        return asyncio.run(_amain(config))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(serve())
