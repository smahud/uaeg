"""Shared runtime state: configuration, store, job and session managers.

One Runtime instance is created by ``uaeg serve`` and shared by the MCP app
and the dashboard app (PRD section 4: "The server and dashboard share the
same backend and configuration")."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from .config import Config
from .db import Store
from .executor.jobs import JobManager
from .executor.pty import SessionManager

log = logging.getLogger("uaeg.runtime")


class Runtime:
    def __init__(self, config: Config, store: Store):
        self.config = config
        self.store = store
        self.jobs = JobManager(store, shell=config.exec.shell)
        self.sessions = SessionManager(
            store,
            shell=config.exec.shell,
            buffer_bytes=config.exec.pty_buffer_bytes,
            max_sessions=config.exec.max_sessions,
        )
        self.started_at = time.time()

    # ------------------------------------------------------------------
    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

    def mcp_url(self) -> str:
        return self.config.mcp_url()

    def dashboard_url(self) -> str:
        bind = self.config.dashboard.bind
        host = "127.0.0.1" if bind in ("0.0.0.0", "::", "*") else bind
        return f"http://{host}:{self.config.dashboard.port}"

    def exec_user(self) -> str:
        import getpass

        try:
            return getpass.getuser()
        except Exception:  # noqa: BLE001
            return str(os.getuid())

    def usage_rules(self) -> list[str]:
        """Advisory usage rules communicated to agents (PRD FR-014).

        These are NOT a security boundary; see docs/LIMITATIONS.md.
        """
        rules = [
            "You are executing on a real Linux host with the privileges of the "
            f"UAEG service user ({self.exec_user()}). Commands have real, "
            "persistent effects.",
            "Prefer non-interactive commands via shell_exec; use PTY sessions "
            "(shell_open/shell_input/shell_output) only for genuinely "
            "interactive programs.",
            "For long-running commands, pass background=true and poll "
            "job_status/job_output instead of blocking.",
        ]
        if self.config.exec.restricted_paths:
            paths = ", ".join(self.config.exec.restricted_paths)
            rules.append(
                f"Advisory restriction (NOT enforced security): the operator asks "
                f"agents to avoid modifying these paths: {paths}."
            )
        return rules

    def check_restricted_cwd(self, cwd: str | None, key: dict[str, Any] | None) -> str | None:
        """Soft restriction check: refuse restricted-permission keys from
        running with a working directory inside an advisory-restricted path.

        Returns an error message when denied, else None. This is explicitly
        advisory (PRD FR-014): a key with mode 'all' is never restricted, and
        any root shell can bypass path checks.
        """
        if not cwd or not key:
            return None
        if key.get("permission_mode") != "restricted":
            return None
        import posixpath

        norm_cwd = posixpath.normpath(os.path.expanduser(cwd))
        for raw in self.config.exec.restricted_paths:
            norm_r = posixpath.normpath(os.path.expanduser(raw))
            if norm_cwd == norm_r or norm_cwd.startswith(norm_r.rstrip("/") + "/"):
                return (
                    f"denied: cwd '{cwd}' is inside an operator-configured "
                    f"advisory restricted path ('{raw}'); run from a different "
                    "working directory (this is a soft, advisory restriction, "
                    "not a security boundary)"
                )
        return None

    def tls_info(self) -> dict[str, Any]:
        """Best-effort certificate summary for doctor/dashboard."""
        info: dict[str, Any] = {"mode": self.config.tls.mode}
        cert = self._resolve_cert_path()
        info["cert_path"] = cert
        if cert:
            try:
                import subprocess

                out = subprocess.run(
                    [
                        "openssl", "x509", "-in", cert, "-noout",
                        "-subject", "-issuer", "-enddate",
                        "-ext", "subjectAltName",
                    ],
                    capture_output=True, text=True, timeout=10,
                )
                for line in out.stdout.splitlines():
                    if line.startswith("subject="):
                        info["subject"] = line.split("=", 1)[1]
                    elif line.startswith("issuer="):
                        info["issuer"] = line.split("=", 1)[1]
                    elif line.startswith("notAfter="):
                        info["expires"] = line.split("=", 1)[1]
                    elif line.strip() and not line.startswith("X509v3"):
                        info.setdefault("san", line.strip())
            except Exception as exc:  # noqa: BLE001
                info["error"] = str(exc)
        return info

    def _resolve_cert_path(self) -> str | None:
        tls = self.config.tls
        if tls.mode == "manual":
            return tls.cert
        if tls.mode == "certbot":
            return (
                f"/etc/letsencrypt/live/{self.config.public_host}/fullchain.pem"
            )
        from . import paths

        return str(paths.tls_dir() / "self-signed.crt")

    async def terminate_key_resources(self, key_id: str) -> dict[str, int]:
        """Revocation containment (PRD FR-012): terminate every live PTY
        session and running background job owned by the key.

        Called by the dashboard when a key is revoked (unless the operator
        explicitly opts out with keep_processes). The CLI revocation path
        uses uaeg.containment (out-of-process signals) for the same effect.
        Rotation is intentionally NOT containment.
        """
        key = self.store.get_key(key_id)
        key_name = key.get("name") if key else None
        sessions = await self.sessions.close_by_key(key_id)
        jobs = await self.jobs.cancel_by_key(key_id)
        if sessions or jobs:
            log.warning(
                "containment for key %s: terminated %d session(s), %d job(s)",
                key_id, sessions, jobs,
            )
            try:
                self.store.add_log(
                    key_id=key_id,
                    key_name=key_name,
                    tool="revocation",
                    status="contained",
                    meta={"terminated_sessions": sessions, "terminated_jobs": jobs},
                )
            except Exception:  # noqa: BLE001 - auditing must never break execution
                log.exception("containment audit write failed")
        return {"terminated_sessions": sessions, "terminated_jobs": jobs}

    async def shutdown(self):
        try:
            await self.jobs.shutdown()
        except Exception:  # noqa: BLE001
            pass
