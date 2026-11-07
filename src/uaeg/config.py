"""UAEG persistent configuration (PRD FR-003).

The configuration lives in a JSON file (default /etc/uaeg/config.json) and
persists across restarts and upgrades. All listeners, TLS behavior and
execution defaults are defined here. The installer writes the initial file;
the dashboard Permissions section and ``uaeg config set`` mutate it.
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_MCP_PORT = 3902
DEFAULT_DASHBOARD_PORT = 3903

TLS_MODES = ("certbot", "manual", "self-signed")
PERMISSION_MODES = ("all", "restricted")

WILDCARD_BINDS = ("0.0.0.0", "::", "*")


class ConfigError(Exception):
    """Raised when configuration is missing, unreadable or invalid."""


@dataclass
class McpConfig:
    bind: str = "0.0.0.0"
    port: int = DEFAULT_MCP_PORT


@dataclass
class DashboardConfig:
    bind: str = "127.0.0.1"
    port: int = DEFAULT_DASHBOARD_PORT
    # Explicit, loud opt-in required before the dashboard may bind publicly.
    # The dashboard has no login; binding it publicly is almost always wrong.
    allow_public: bool = False


@dataclass
class TlsConfig:
    mode: str = "self-signed"  # certbot | manual | self-signed
    cert: str | None = None
    key: str | None = None


@dataclass
class ExecConfig:
    shell: str = "/bin/bash"
    default_cwd: str = "~"
    default_timeout_s: int = 120
    max_output_bytes: int = 1_048_576  # per stream, sync shell_exec
    max_sessions: int = 16
    pty_buffer_bytes: int = 262_144  # bounded in-memory PTY ring buffer
    restricted_paths: list[str] = field(default_factory=list)


@dataclass
class NetworkConfig:
    # Optional additional source-IP allowlist (CIDR) for dashboard requests.
    allowed_dashboard_cidrs: list[str] | None = None


@dataclass
class Config:
    public_host: str = "localhost"
    mcp: McpConfig = field(default_factory=McpConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    tls: TlsConfig = field(default_factory=TlsConfig)
    exec: ExecConfig = field(default_factory=ExecConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        cfg = cls()
        try:
            cfg.public_host = str(data.get("public_host", cfg.public_host))
            cfg.mcp = _merge_section(McpConfig, data, "mcp")
            cfg.dashboard = _merge_section(DashboardConfig, data, "dashboard")
            cfg.tls = _merge_section(TlsConfig, data, "tls")
            cfg.exec = _merge_section(ExecConfig, data, "exec")
            cfg.network = _merge_section(NetworkConfig, data, "network")
        except TypeError as exc:
            raise ConfigError(
                f"configuration contains an unknown or mistyped field: {exc}. "
                "Remove fields that do not belong to this UAEG version."
            ) from exc
        return cfg

    def save(self, path: Path | str | None = None) -> None:
        from . import paths

        target = Path(path) if path else paths.config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        from . import paths

        target = Path(path) if path is not None else paths.config_path()
        if not target.exists():
            raise ConfigError(
                f"Configuration file not found: {target}\n"
                "Run the installer (sudo ./install.sh) or create one with: "
                "uaeg init"
            )
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Configuration file {target} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"Configuration file {target} must contain a JSON object")
        cfg = cls.from_dict(data)
        cfg.validate()
        return cfg

    # ------------------------------------------------------------------
    # Validation & derived values
    # ------------------------------------------------------------------
    def validate(self) -> list[str]:
        """Validate the configuration; raise ConfigError on fatal problems.

        Returns a list of human-readable warnings (non-fatal).
        """
        warnings: list[str] = []

        def _port_ok(value: Any) -> bool:
            # bool is an int subclass in Python; True must not pass as port 1.
            return (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 1 <= value <= 65535
            )

        if not _port_ok(self.mcp.port):
            raise ConfigError(f"mcp.port must be an integer in 1..65535 (got {self.mcp.port!r})")
        if not _port_ok(self.dashboard.port):
            raise ConfigError(
                f"dashboard.port must be an integer in 1..65535 (got {self.dashboard.port!r})"
            )
        if self.mcp.port == self.dashboard.port:
            raise ConfigError(
                f"mcp.port and dashboard.port must differ (both are {self.mcp.port})"
            )
        if not self.public_host or not isinstance(self.public_host, str):
            raise ConfigError("public_host must be a non-empty string (domain or public IP)")

        if self.tls.mode not in TLS_MODES:
            raise ConfigError(f"tls.mode must be one of {TLS_MODES} (got {self.tls.mode!r})")
        if self.tls.mode == "manual":
            if not self.tls.cert or not self.tls.key:
                raise ConfigError("tls.mode 'manual' requires tls.cert and tls.key paths")
            if not Path(self.tls.cert).is_file():
                raise ConfigError(f"tls.cert file not found: {self.tls.cert}")
            if not Path(self.tls.key).is_file():
                raise ConfigError(f"tls.key file not found: {self.tls.key}")
        if self.tls.mode == "certbot" and self.public_host in ("localhost",):
            raise ConfigError("tls.mode 'certbot' requires a real public_host domain")

        if self.dashboard.bind in WILDCARD_BINDS and not self.dashboard.allow_public:
            raise ConfigError(
                "Refusing to bind the dashboard to all interfaces: the dashboard has "
                "no login. Bind it to a LAN IP or 127.0.0.1, or set "
                "dashboard.allow_public=true explicitly if you really accept the risk."
            )
        if self.dashboard.bind in WILDCARD_BINDS:
            warnings.append(
                "Dashboard is bound to ALL interfaces with allow_public=true. "
                "Anyone who can reach this port gets full administration access."
            )

        if self.exec.default_timeout_s <= 0:
            raise ConfigError("exec.default_timeout_s must be positive")
        if self.exec.max_output_bytes < 1024:
            raise ConfigError("exec.max_output_bytes must be at least 1024")
        if self.exec.max_sessions < 1:
            raise ConfigError("exec.max_sessions must be at least 1")
        if self.exec.pty_buffer_bytes < 4096:
            raise ConfigError("exec.pty_buffer_bytes must be at least 4096")
        if self.exec.restricted_paths:
            warnings.append(
                "Advisory restricted paths are configured. These are usage rules "
                "communicated to agents - they are NOT a security boundary "
                "(PRD FR-014)."
            )
        if self.tls.mode == "self-signed":
            warnings.append(
                "TLS mode is self-signed: hosted MCP clients will likely reject "
                "this certificate. Use a domain + Let's Encrypt (certbot) or "
                "provide trusted certificates for real agent connections."
            )

        # Dashboard CIDR allowlist: an unparseable entry must never silently
        # weaken the access control (audit finding: if every entry fails to
        # parse, the allowlist used to disappear entirely - fail closed
        # instead, at configuration load time).
        cidrs = self.network.allowed_dashboard_cidrs
        if cidrs is not None:
            if not isinstance(cidrs, list) or not all(
                isinstance(c, str) for c in cidrs
            ):
                raise ConfigError(
                    "network.allowed_dashboard_cidrs must be a list of CIDR "
                    "strings (e.g. [\"192.168.1.0/24\"], or null to disable)"
                )
            bad = [c for c in cidrs if not _valid_cidr(c)]
            if bad:
                raise ConfigError(
                    "network.allowed_dashboard_cidrs contains invalid CIDR "
                    "entries: "
                    + ", ".join(repr(c) for c in bad)
                    + ". Refusing to start: an unparseable dashboard allowlist "
                    "must not silently disable access control. Fix or remove "
                    "the entries, or set the value to null."
                )
        return warnings

    def mcp_url(self) -> str:
        return f"https://{self.public_host}:{self.mcp.port}/mcp"

    def with_updates(self, **changes: Any) -> "Config":
        return replace(self, **changes)


def _sub(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def _merge_section(section_cls: type, data: dict[str, Any], key: str):
    """Construct a config section from defaults + the file's overrides."""
    defaults = asdict(section_cls())
    return section_cls(**{**defaults, **_sub(data, key)})


def _valid_cidr(raw: str) -> bool:
    try:
        ipaddress.ip_network(raw, strict=False)
        return True
    except ValueError:
        return False
