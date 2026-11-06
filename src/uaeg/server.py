"""Server entrypoint: ``uaeg serve``.

Runs ONE process with TWO listeners (PRD section 4, FR-018):

- MCP listener: public HTTPS (TLS required; explicit UAEG_DEV_INSECURE_HTTP=1
  escape hatch for local development only).
- Dashboard listener: private LAN/loopback HTTP, never public by default.

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


def _build_ssl_context(config: Config) -> ssl.SSLContext | None:
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

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert_path, key_path)
    return cert_path, key_path, ctx


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
    store = Store(paths.database_path())
    runtime = Runtime(config, store)

    # Reconcile persisted state with a possibly-restarted server.
    job_counts = runtime.jobs.reattach()
    session_count = runtime.sessions.startup_reconcile()
    log.info(
        "startup reconciliation: %s jobs, %d sessions from previous run",
        job_counts, session_count,
    )

    mcp_app = build_mcp_asgi_app(runtime)
    dashboard_app = build_dashboard_asgi_app(runtime)

    tls_result = _build_ssl_context(config)
    cert_path, key_path, ssl_ctx = tls_result if tls_result else (None, None, None)
    insecure = os.environ.get("UAEG_DEV_INSECURE_HTTP") == "1"
    if ssl_ctx is None and not insecure:
        raise ConfigError(
            "no TLS configuration available for the public MCP endpoint; "
            "refusing to serve plain HTTP (set UAEG_DEV_INSECURE_HTTP=1 only "
            "for isolated local development)"
        )
    if ssl_ctx is None and insecure:
        log.warning("UAEG_DEV_INSECURE_HTTP=1 set: serving MCP over plain HTTP (dev only!)")

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
        "UAEG v%s starting: MCP %s (tls=%s) | dashboard http://%s:%d | data=%s",
        __version__,
        runtime.mcp_url(),
        "on" if ssl_ctx else "OFF(dev)",
        config.dashboard.bind,
        config.dashboard.port,
        paths.data_dir(),
    )

    mcp_task = asyncio.create_task(mcp_server.serve(), name="uaeg-mcp")
    dash_task = asyncio.create_task(dashboard_server.serve(), name="uaeg-dashboard")

    try:
        await stop
    finally:
        for task in (mcp_task, dash_task):
            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=10)
                except asyncio.TimeoutError:
                    task.cancel()
        await runtime.sessions.shutdown()
        runtime.jobs.shutdown()
        store.close()

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
