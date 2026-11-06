"""LAN administration dashboard (PRD FR-015, FR-016, FR-018).

A single-page application served on the private listener. Five sections:
Keys, Permissions, Sessions, Logs, Server. No application login in the MVP;
network placement (bind address + optional CIDR allowlist) is the boundary.

The JSON API below is consumed by the embedded UI (dashboard_ui.TEMPLATE).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import platform
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from . import __version__, paths, security
from .config import PERMISSION_MODES, ConfigError
from .dashboard_ui import TEMPLATE
from .runtime import Runtime

log = logging.getLogger("uaeg.dashboard")


def _json_ok(payload: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def build_dashboard_asgi_app(runtime: Runtime) -> Starlette:
    store = runtime.store

    # ------------------------------------------------------------------
    # Optional source-IP allowlist (in addition to the bind address).
    # ------------------------------------------------------------------
    allowed_cidrs: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    raw_cidrs = runtime.config.network.allowed_dashboard_cidrs or []
    for raw in raw_cidrs:
        try:
            allowed_cidrs.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            log.warning("ignoring invalid dashboard CIDR: %r", raw)

    async def check_client(request: Request, call_next):
        if request.url.path.startswith("/api") or request.url.path == "/":
            peer = request.client.host if request.client else ""
            if allowed_cidrs and not _peer_allowed(peer):
                log.warning("dashboard request from %s rejected by CIDR allowlist", peer)
                return JSONResponse(
                    {"error": "forbidden: source address not in dashboard allowlist"},
                    status_code=403,
                )
        return await call_next(request)

    def _peer_allowed(peer: str) -> bool:
        try:
            addr = ipaddress.ip_address(peer)
        except ValueError:
            return False
        return any(addr in net for net in allowed_cidrs)

    # ------------------------------------------------------------------
    # HTML
    # ------------------------------------------------------------------
    async def index(request: Request) -> Response:
        return HTMLResponse(TEMPLATE)

    # ------------------------------------------------------------------
    # overview
    # ------------------------------------------------------------------
    async def overview(request: Request) -> Response:
        return _json_ok(
            {
                "service": "UAEG",
                "version": __version__,
                "uptime_seconds": round(runtime.uptime_seconds, 1),
                "hostname": platform.node(),
                "exec_user": runtime.exec_user(),
                "mcp_url": runtime.mcp_url(),
                "dashboard_url": runtime.dashboard_url(),
                "now_utc": _now(),
            }
        )

    # ------------------------------------------------------------------
    # keys
    # ------------------------------------------------------------------
    async def list_keys(request: Request) -> Response:
        return _json_ok({"keys": store.list_keys()})

    async def create_key(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _err("invalid JSON body")
        name = str(body.get("name") or "").strip() or "Unnamed agent"
        mode = str(body.get("permission_mode") or "all")
        if mode not in PERMISSION_MODES:
            return _err(f"permission_mode must be one of {PERMISSION_MODES}")
        material = security.new_key(name)
        key = store.create_key(
            key_id=material.key_id,
            name=name,
            prefix=material.prefix,
            verifier_hex=material.verifier,
            permission_mode=mode,
        )
        log.info("dashboard created key %s (%s, mode=%s)", material.key_id, name, mode)
        return _json_ok({"key": _key_public(key), "secret": material.secret}, status=201)

    async def revoke_key(request: Request) -> Response:
        key_id = request.path_params["key_id"]
        key = store.revoke_key(key_id)
        if not key:
            return _err(f"unknown key_id: {key_id}", 404)
        log.info("dashboard revoked key %s", key_id)
        return _json_ok({"key": _key_public(key)})

    async def rotate_key(request: Request) -> Response:
        key_id = request.path_params["key_id"]
        existing = store.get_key(key_id)
        if not existing:
            return _err(f"unknown key_id: {key_id}", 404)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        name = str(body.get("name") or existing["name"])
        material = security.new_key(name)
        store.revoke_key(key_id)
        new_key = store.create_key(
            key_id=material.key_id,
            name=name,
            prefix=material.prefix,
            verifier_hex=material.verifier,
            permission_mode=existing.get("permission_mode", "all"),
        )
        log.info("dashboard rotated key %s -> %s", key_id, material.key_id)
        return _json_ok({"key": _key_public(new_key), "secret": material.secret})

    async def set_key_permissions(request: Request) -> Response:
        key_id = request.path_params["key_id"]
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _err("invalid JSON body")
        mode = str(body.get("permission_mode") or "")
        if mode not in PERMISSION_MODES:
            return _err(f"permission_mode must be one of {PERMISSION_MODES}")
        key = store.set_key_permission(key_id, mode)
        if not key:
            return _err(f"unknown key_id: {key_id}", 404)
        return _json_ok({"key": _key_public(key)})

    # ------------------------------------------------------------------
    # permissions (advisory restricted paths)
    # ------------------------------------------------------------------
    async def get_permissions(request: Request) -> Response:
        return _json_ok(_permissions_payload())

    async def set_permissions(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _err("invalid JSON body")
        raw = body.get("restricted_paths")
        if raw is None:
            return _err("restricted_paths is required")
        if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
            return _err("restricted_paths must be a list of strings")
        cleaned: list[str] = []
        for p in raw:
            p = p.strip()
            if p and p not in cleaned:
                cleaned.append(p)
        runtime.config.exec.restricted_paths = cleaned
        try:
            runtime.config.save()
        except OSError as exc:
            return _err(f"failed to persist config: {exc}", 500)
        log.info("advisory restricted paths updated: %s", cleaned)
        return _json_ok(_permissions_payload())

    def _permissions_payload() -> dict:
        return {
            "default_mode": "all",
            "restricted_paths": list(runtime.config.exec.restricted_paths),
            "note": (
                "Advisory restricted paths are usage rules communicated to agents. "
                "They are NOT a security boundary: any root shell can bypass them "
                "(PRD FR-014)."
            ),
        }

    # ------------------------------------------------------------------
    # sessions & jobs
    # ------------------------------------------------------------------
    async def list_sessions(request: Request) -> Response:
        live = []
        for s in runtime.sessions.list():
            entry = dict(s)
            entry["preview_tail"] = runtime.sessions.get(s["session_id"]).preview_tail(160)
            live.append(entry)
        history = store.list_sessions(limit=100)
        return _json_ok({"live": live, "history": history})

    async def terminate_session(request: Request) -> Response:
        session_id = request.path_params["session_id"]
        try:
            await runtime.sessions.terminate_by_id(session_id)
            return _json_ok({"ok": True, "status": "closed"})
        except Exception as exc:  # noqa: BLE001 - SessionError
            return _err(str(exc), 404)

    async def list_jobs(request: Request) -> Response:
        limit = _int_param(request, "limit", 50, 1, 500)
        return _json_ok({"jobs": runtime.jobs.list(limit=limit)})

    async def cancel_job(request: Request) -> Response:
        job_id = request.path_params["job_id"]
        try:
            job = await runtime.jobs.cancel(job_id)
            return _json_ok({"ok": True, "job": job})
        except Exception as exc:  # noqa: BLE001 - JobError
            return _err(str(exc), 404)

    async def job_output(request: Request) -> Response:
        job_id = request.path_params["job_id"]
        stream = request.query_params.get("stream", "stdout")
        offset = _int_param(request, "offset", 0, 0, 1 << 40)
        limit = _int_param(request, "limit", 65536, 1, 1 << 20)
        try:
            return _json_ok(runtime.jobs.output(job_id, stream, offset, limit))
        except Exception as exc:  # noqa: BLE001 - JobError
            return _err(str(exc), 404)

    # ------------------------------------------------------------------
    # logs
    # ------------------------------------------------------------------
    async def list_logs(request: Request) -> Response:
        limit = _int_param(request, "limit", 100, 1, 1000)
        offset = _int_param(request, "offset", 0, 0, 1 << 30)
        return _json_ok({"logs": store.list_logs(limit=limit, offset=offset)})

    # ------------------------------------------------------------------
    # server
    # ------------------------------------------------------------------
    async def server_info(request: Request) -> Response:
        cfg = runtime.config
        return _json_ok(
            {
                "version": __version__,
                "uptime_seconds": round(runtime.uptime_seconds, 1),
                "config_path": str(paths.config_path()),
                "data_dir": str(paths.data_dir()),
                "mcp": {
                    "bind": cfg.mcp.bind,
                    "port": cfg.mcp.port,
                    "url": runtime.mcp_url(),
                    "tls_mode": cfg.tls.mode,
                },
                "dashboard": {"bind": cfg.dashboard.bind, "port": cfg.dashboard.port},
                "tls": runtime.tls_info(),
                "summary": store.summary(),
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "service_user": runtime.exec_user(),
                "hostname": platform.node(),
            }
        )

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/api/overview", overview, methods=["GET"]),
        Route("/api/keys", list_keys, methods=["GET"]),
        Route("/api/keys", create_key, methods=["POST"]),
        Route("/api/keys/{key_id}/revoke", revoke_key, methods=["POST"]),
        Route("/api/keys/{key_id}/rotate", rotate_key, methods=["POST"]),
        Route("/api/keys/{key_id}/permissions", set_key_permissions, methods=["POST"]),
        Route("/api/permissions", get_permissions, methods=["GET"]),
        Route("/api/permissions", set_permissions, methods=["POST"]),
        Route("/api/sessions", list_sessions, methods=["GET"]),
        Route("/api/sessions/{session_id}/terminate", terminate_session, methods=["POST"]),
        Route("/api/jobs", list_jobs, methods=["GET"]),
        Route("/api/jobs/{job_id}/cancel", cancel_job, methods=["POST"]),
        Route("/api/jobs/{job_id}/output", job_output, methods=["GET"]),
        Route("/api/logs", list_logs, methods=["GET"]),
        Route("/api/server", server_info, methods=["GET"]),
    ]

    app = Starlette(routes=routes)
    if allowed_cidrs:
        app.add_middleware(_ClientCheckMiddleware, peer_allowed=_peer_allowed)
    return app


def _key_public(key: dict[str, Any]) -> dict[str, Any]:
    return {
        "key_id": key.get("key_id"),
        "name": key.get("name"),
        "prefix": key.get("prefix"),
        "status": key.get("status"),
        "permission_mode": key.get("permission_mode"),
        "created_at": key.get("created_at"),
        "last_used_at": key.get("last_used_at"),
        "revoked_at": key.get("revoked_at"),
    }


def _now() -> str:
    from .db import utcnow

    return utcnow()


def _int_param(request: Request, name: str, default: int, lo: int, hi: int) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


class _ClientCheckMiddleware:
    """Starlette middleware enforcing the optional CIDR allowlist."""

    def __init__(self, app, peer_allowed):
        self.app = app
        self._peer_allowed = peer_allowed

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            peer = scope.get("client", (None, None))[0] or ""
            if not self._peer_allowed(peer):
                from starlette.responses import JSONResponse

                resp = JSONResponse(
                    {"error": "forbidden: source address not in dashboard allowlist"},
                    status_code=403,
                )
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)
