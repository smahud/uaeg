"""Administration dashboard (PRD FR-015, FR-016, FR-018).

A single-page application served on the dashboard listener. Five sections:
Keys, Permissions, Sessions, Logs, Server.

Authentication (v0.3.0): the dashboard has a native password login.
- When a dashboard password exists (created with ``uaeg pass``), EVERY page
  and every API route requires a valid server-side session; unauthenticated
  browsers are redirected to ``/login`` and unauthenticated API calls get
  ``401``.
- When NO password exists, the dashboard behaves exactly like v0.2.0 on a
  private bind (no login). A public (wildcard) bind is refused at serve
  time unless a password AND TLS are configured (fail closed).
- Login attempts are throttled per source IP, audited (without secrets),
  and protected by a session-bound CSRF token plus Origin validation.

The JSON API below is consumed by the embedded UI (dashboard_ui.TEMPLATE).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import platform
from datetime import datetime, timezone
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import __version__, dashauth, paths, security
from .config import PERMISSION_MODES, ConfigError
from .dashboard_ui import LOGIN_TEMPLATE, TEMPLATE
from .runtime import Runtime

log = logging.getLogger("uaeg.dashboard")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _json_ok(payload: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


class _BodyTooLarge(Exception):
    pass


class _ClientDisconnected(Exception):
    pass


async def _read_json_capped(request: Request, limit: int) -> dict:
    """Read and parse a JSON request body with a hard size cap.

    Enforces the limit even for chunked requests that omit a Content-Length
    header.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            raise _ClientDisconnected()
        if message["type"] != "http.request":
            break
        data = message.get("body", b"")
        total += len(data)
        if total > limit:
            raise _BodyTooLarge()
        chunks.append(data)
        if not message.get("more_body", False):
            break
    raw = b"".join(chunks)
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("body must be a JSON object")
    return parsed


def _cookie_value(cookie_header: str | None, name: str) -> str | None:
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        if "=" in part:
            key, _, value = part.partition("=")
            if key.strip() == name:
                return value.strip()
    return None


def _origin_allowed(origin: str | None, host: str | None) -> bool:
    """Validate the Origin header of an unsafe-method request.

    An absent Origin is allowed (SameSite + CSRF token still apply); a
    present Origin must match the request's Host (scheme-agnostic, direct
    access without proxies).
    """
    if not origin:
        return True
    if origin.lower() == "null":
        return False
    try:
        from urllib.parse import urlparse

        origin_host = urlparse(origin).netloc.lower()
    except ValueError:
        return False
    if not origin_host:
        return False
    return bool(host) and origin_host == host.lower()


# ----------------------------------------------------------------------
# Shared per-app auth context
# ----------------------------------------------------------------------
class _AuthContext:
    def __init__(self, runtime: Runtime):
        self.passwords = dashauth.PasswordManager()
        self.sessions = dashauth.SessionStore(
            idle_timeout_s=runtime.config.dashboard.session_idle_timeout_s,
            absolute_timeout_s=runtime.config.dashboard.session_absolute_timeout_s,
        )
        self.throttle = dashauth.LoginThrottle()


class _DashAuthMiddleware:
    """ASGI middleware enforcing the dashboard login on EVERY endpoint.

    - ``/login`` stays public (GET page, POST credential check).
    - Everything else requires a valid server-side session: browsers are
      redirected to ``/login``; API calls receive ``401``.
    - State-changing requests additionally require the session's CSRF token
      (``X-CSRF-Token``) and a consistent ``Origin``.
    - Requests with an oversized declared body are rejected with ``413``.
    - When no password is configured the middleware is a pass-through
      (legacy v0.2.0 mode, only reachable on a private bind because a
      wildcard bind without a password is refused at serve time).
    """

    def __init__(self, app, auth: _AuthContext):
        self.app = app
        self.auth = auth

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        if not self.auth.passwords.is_configured():
            # Legacy unauthenticated mode - serve() guarantees this branch
            # is unreachable on a public bind (password required there).
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }

        if path == "/login":
            await self.app(scope, receive, send)
            return

        token = _cookie_value(headers.get("cookie"), dashauth.SESSION_COOKIE)
        session = self.auth.sessions.get_valid(
            token, self.auth.passwords.generation()
        )
        if session is None:
            if path.startswith("/api/"):
                await _send_json(
                    send, 401, {"error": "authentication required"}, www_authenticate=True
                )
            else:
                await _send_redirect(send, "/login")
            return

        if method in UNSAFE_METHODS:
            if not _origin_allowed(headers.get("origin"), headers.get("host")):
                await _send_json(send, 403, {"error": "request origin not allowed"})
                return
            supplied = headers.get("x-csrf-token", "")
            if not supplied or not security.constant_time_equals(
                supplied, session.csrf_token
            ):
                await _send_json(send, 403, {"error": "CSRF token missing or invalid"})
                return

        content_length = headers.get("content-length")
        if content_length and content_length.isdigit():
            if int(content_length) > dashauth.MAX_BODY_BYTES:
                await _send_json(send, 413, {"error": "request body too large"})
                return

        scope.setdefault("state", {})["uaeg_dash_session"] = session
        await self.app(scope, receive, send)


async def _send_json(
    send, status: int, payload: dict, www_authenticate: bool = False
) -> None:
    body = json.dumps(payload).encode("utf-8")
    headers = [(b"content-type", b"application/json")]
    if www_authenticate:
        headers.append((b"www-authenticate", b'Bearer realm="uaeg-dashboard"'))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _send_redirect(send, location: str) -> None:
    headers = [(b"location", location.encode("utf-8"))]
    await send({"type": "http.response.start", "status": 302, "headers": headers})
    await send({"type": "http.response.body", "body": b""})


def build_dashboard_asgi_app(runtime: Runtime) -> Starlette:
    store = runtime.store
    auth = _AuthContext(runtime)

    # ------------------------------------------------------------------
    # Optional source-IP allowlist (in addition to the bind address).
    # ------------------------------------------------------------------
    allowed_cidrs: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    raw_cidrs = runtime.config.network.allowed_dashboard_cidrs or []
    for raw in raw_cidrs:
        try:
            allowed_cidrs.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            # Config.validate() already rejects invalid entries at load
            # time; this guard covers programmatic construction paths. The
            # dashboard must FAIL CLOSED - never silently drop the list.
            raise ConfigError(
                f"invalid dashboard CIDR {raw!r}: refusing to serve the "
                "dashboard with a broken allowlist (fail closed)"
            )

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
    # login / logout / session info
    # ------------------------------------------------------------------
    async def login_page(request: Request) -> Response:
        # Already authenticated? Straight to the dashboard.
        token = request.cookies.get(dashauth.SESSION_COOKIE)
        session = auth.sessions.get_valid(
            token, auth.passwords.generation()
        )
        if session is not None:
            return RedirectResponse("/", status_code=302)
        configured = auth.passwords.is_configured() and not auth.passwords.is_broken()
        html = (
            LOGIN_TEMPLATE
            .replace("__PASSWORD_CONFIGURED__", "true" if configured else "false")
            .replace("__AUTH_BROKEN__", "true" if auth.passwords.is_broken() else "false")
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    async def login_post(request: Request) -> Response:
        peer = request.client.host if request.client else "unknown"
        origin = request.headers.get("origin")
        if not _origin_allowed(origin, request.headers.get("host")):
            return _err("request origin not allowed", 403)

        locked = auth.throttle.locked_for(peer)
        if locked > 0:
            try:
                store.add_log(
                    None, "operator", "dashboard_login", "locked",
                    meta={"ip": peer, "retry_after_s": locked},
                )
            except Exception:  # noqa: BLE001 - auditing must never break login
                pass
            log.warning("dashboard login rejected: source %s is locked out", peer)
            resp = JSONResponse(
                {"error": "Too many failed attempts. Try again later."},
                status_code=429,
            )
            resp.headers["Retry-After"] = str(locked)
            return resp

        try:
            body = await _read_json_capped(request, dashauth.MAX_LOGIN_BODY_BYTES)
        except _BodyTooLarge:
            return _err("request body too large", 413)
        except _ClientDisconnected:
            return _err("client disconnected", 400)
        except (ValueError, json.JSONDecodeError):
            return _err("invalid JSON body")

        password = body.get("password")
        if auth.passwords.is_broken():
            log.error("dashboard login denied: password file unreadable")
            return _err(
                "dashboard password storage error - run 'sudo uaeg pass' "
                "on the server to repair it",
                503,
            )
        if not isinstance(password, str) or not auth.passwords.verify(password):
            triggered = auth.throttle.record_failure(peer)
            try:
                store.add_log(None, "operator", "dashboard_login", "denied", meta={"ip": peer})
            except Exception:  # noqa: BLE001
                pass
            log.warning(
                "dashboard login failed from %s%s", peer,
                " - source IP locked out for brute-force protection" if triggered else "",
            )
            return _err("Login failed.", 401)

        auth.throttle.record_success(peer)
        session = auth.sessions.create(auth.passwords.generation() or "")
        try:
            store.add_log(None, "operator", "dashboard_login", "ok", meta={"ip": peer})
        except Exception:  # noqa: BLE001
            pass
        log.info("dashboard login succeeded from %s (new session issued)", peer)
        secure = request.url.scheme == "https" or runtime.config.dashboard.tls
        resp = JSONResponse({"ok": True, "expires_in_s": auth.sessions.idle_timeout_s})
        resp.set_cookie(
            dashauth.SESSION_COOKIE,
            session.token,
            max_age=int(auth.sessions.idle_timeout_s),
            httponly=True,
            samesite="lax",
            secure=secure,
            path="/",
        )
        return resp

    async def logout(request: Request) -> Response:
        session = request.scope.get("state", {}).get("uaeg_dash_session")
        if session is not None:
            auth.sessions.revoke(session.token)
            try:
                peer = request.client.host if request.client else "unknown"
                store.add_log(None, "operator", "dashboard_logout", "ok", meta={"ip": peer})
            except Exception:  # noqa: BLE001
                pass
            log.info("dashboard session logged out")
        secure = request.url.scheme == "https" or runtime.config.dashboard.tls
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(
            dashauth.SESSION_COOKIE, path="/", secure=secure, samesite="lax"
        )
        return resp

    async def whoami(request: Request) -> Response:
        session = request.scope["state"]["uaeg_dash_session"]
        expires_at = datetime.fromtimestamp(
            session.created_at + auth.sessions.absolute_timeout_s, tz=timezone.utc
        )
        return _json_ok(
            {
                "authenticated": True,
                "csrf_token": session.csrf_token,
                "idle_timeout_s": auth.sessions.idle_timeout_s,
                "absolute_timeout_s": auth.sessions.absolute_timeout_s,
                "session_expires_at": expires_at.isoformat(timespec="seconds"),
            }
        )

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
        if not store.get_key(key_id):
            return _err(f"unknown key_id: {key_id}", 404)
        keep_processes = False
        try:
            body = await request.json()
            if isinstance(body, dict):
                keep_processes = bool(body.get("keep_processes", False))
        except Exception:  # noqa: BLE001 - body optional
            pass
        key = store.revoke_key(key_id)
        if not key:
            return _err(f"unknown key_id: {key_id}", 404)
        # Revocation containment: a revoked (possibly compromised) agent
        # must not keep live sessions or background jobs running. Operators
        # can opt out per revocation with keep_processes=true.
        containment = {"terminated_sessions": 0, "terminated_jobs": 0}
        if not keep_processes:
            try:
                containment = await runtime.terminate_key_resources(key_id)
            except Exception as exc:  # noqa: BLE001
                containment = {"error": str(exc)}
        log.info(
            "dashboard revoked key %s (containment=%s, keep_processes=%s)",
            key_id, containment, keep_processes,
        )
        return _json_ok({"key": _key_public(key), "containment": containment})

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
            entry["preview_tail"] = security.redact_secrets(
                runtime.sessions.get(s["session_id"]).preview_tail(160)
            )
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
        password_active = auth.passwords.is_configured()
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
                "dashboard": {
                    "bind": cfg.dashboard.bind,
                    "port": cfg.dashboard.port,
                    "tls": cfg.dashboard.tls,
                    "auth_mode": (
                        "password"
                        if password_active
                        else "none (private bind, no password set - run 'sudo uaeg pass')"
                    ),
                    "session_idle_timeout_s": cfg.dashboard.session_idle_timeout_s,
                },
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
        Route("/login", login_page, methods=["GET"]),
        Route("/login", login_post, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/api/whoami", whoami, methods=["GET"]),
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
    # Wrap explicitly so the order is unambiguous: CIDR allowlist (outermost,
    # fail-closed network gate) -> login/session enforcement -> routes.
    app = _DashAuthMiddleware(app, auth)
    if allowed_cidrs:
        app = _ClientCheckMiddleware(app, peer_allowed=_peer_allowed)
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
    """ASGI middleware enforcing the optional CIDR allowlist."""

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
