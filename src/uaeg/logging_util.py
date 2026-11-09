"""Logging setup: structured lines to stdout (captured by the systemd journal)."""

from __future__ import annotations

import logging
import sys

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


class _McpDisconnectNoiseFilter(logging.Filter):
    """Drop the MCP SDK's client-disconnect tracebacks (audit 2026-11-10, F6).

    ``mcp.server.streamable_http`` logs an ERROR record with a full
    ``anyio.ClosedResourceError`` traceback every time an MCP client
    disconnects - which is the NORMAL end of every agent session. On a busy
    gateway the journal fills with these false alarms (8 tracebacks were
    observed in the live journal during a single audit session). Only the
    exact disconnect shape is dropped; genuine transport errors still log.
    """

    NOISY_MESSAGES = ("Error in standalone SSE writer",)
    NOISY_EXCEPTIONS = ("ClosedResourceError", "CancelledError")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.CRITICAL:
            return True  # never drop critical records
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - formatting must not break logging
            return True
        if not any(marker in msg for marker in self.NOISY_MESSAGES):
            return True
        exc = record.exc_info
        if exc and exc[0] is not None:
            if exc[0].__name__ in self.NOISY_EXCEPTIONS:
                return False  # the known benign disconnect traceback
        # Message matched but the exception differs: keep it (real error).
        return True


def _ensure_mcp_noise_filter() -> None:
    """Idempotently attach the disconnect-noise filter (F6)."""
    logger = logging.getLogger("mcp.server.streamable_http")
    if not any(isinstance(f, _McpDisconnectNoiseFilter) for f in logger.filters):
        logger.addFilter(_McpDisconnectNoiseFilter())


def _suppress_pydantic_settings_lifespan_warning() -> None:
    """Idempotently suppress the harmless pydantic-settings warning (F7)."""
    import warnings

    warnings.filterwarnings(
        "ignore",
        message="Field 'lifespan' has an incomplete definition.*",
        category=UserWarning,
    )


def setup_logging(level: str = "INFO") -> None:
    # Applied first so a re-invocation (level change) still guarantees the
    # hardening filters are in place even when handlers already exist.
    _ensure_mcp_noise_filter()
    _suppress_pydantic_settings_lifespan_warning()
    root = logging.getLogger()
    if root.handlers:  # already configured
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(FORMAT, datefmt=DATEFMT))
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Reduce ASGI framework noise
    for noisy in ("uvicorn.access", "uvicorn.error", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
