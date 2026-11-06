"""Bearer authentication for the public MCP endpoint (PRD FR-005).

- The ASGI middleware below guards ONLY the ``/mcp`` route; health and info
  routes stay public.
- Keys are verified per request, so revocation takes effect immediately,
  even for long-lived MCP sessions.
- Only SHA-256 verifiers are stored; the presented token is hashed and
  looked up - the secret itself never touches the database.
- Inside tools, ``request_key()`` re-reads the Authorization header from the
  live request (via MCP Context), giving accurate audit identity even when
  a session outlives a key rotation.
"""

from __future__ import annotations

import logging
from typing import Any

from . import security
from .db import Store

log = logging.getLogger("uaeg.auth")

UNPROTECTED_PATHS = {"/healthz", "/", "/info"}


def extract_bearer_token(headers: Any) -> str | None:
    """Extract the bearer token from mapping-like headers (case-insensitive)."""
    if headers is None:
        return None
    get = headers.get if hasattr(headers, "get") else (lambda k, d=None: d)
    value = get("authorization")
    if value is None:
        # starlette Headers are already case-insensitive; plain dicts are not
        for k, v in (headers.items() if hasattr(headers, "items") else []):
            if str(k).lower() == "authorization":
                value = v
                break
    if not value:
        return None
    value = str(value)
    if not value.lower().startswith("bearer "):
        return None
    token = value[7:].strip()
    return token or None


def resolve_key(token: str | None, store: Store) -> dict[str, Any] | None:
    """Return the active key record for a bearer token, else None."""
    if not token:
        return None
    return store.find_active_key_by_verifier(security.verifier(token))


class BearerAuthMiddleware:
    """ASGI middleware: require a valid agent key on the MCP route."""

    def __init__(self, app, store: Store, protected_path: str = "/mcp"):
        self.app = app
        self.store = store
        self.protected_path = protected_path

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path != self.protected_path:
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        token = extract_bearer_token(headers)
        key = resolve_key(token, self.store)
        if key is None:
            reason = "missing" if not token else "invalid or revoked"
            await _send_json(
                send,
                401,
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32001, "message": f"Unauthorized: {reason} bearer token"},
                    "id": None,
                },
                extra_headers=[(b"www-authenticate", b'Bearer error="invalid_token"')],
            )
            if token:
                log.warning("rejected MCP request with %s bearer token", reason)
            return

        self.store.touch_key_last_used(key["key_id"])
        await self.app(scope, receive, send)


async def _send_json(send, status: int, payload: dict, extra_headers=None) -> None:
    import json as _json

    body = _json.dumps(payload).encode("utf-8")
    headers = [(b"content-type", b"application/json")]
    if extra_headers:
        headers.extend(extra_headers)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


def request_key(ctx, store: Store) -> dict[str, Any] | None:
    """Best-effort identity resolution from inside a tool call.

    Prefers the live HTTP request (fresh per call); falls back to None when
    no request context is available (e.g. direct programmatic invocation).
    """
    try:
        request = getattr(ctx, "request_context", None)
        request = getattr(request, "request", None) if request else None
        if request is not None:
            headers = getattr(request, "headers", None)
            token = extract_bearer_token(headers)
            return resolve_key(token, store)
    except Exception:  # noqa: BLE001 - identity is best-effort
        log.debug("request_key fallback failed", exc_info=True)
    return None
