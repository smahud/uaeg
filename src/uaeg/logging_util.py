"""Logging setup: structured lines to stdout (captured by the systemd journal)."""

from __future__ import annotations

import logging
import sys

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def setup_logging(level: str = "INFO") -> None:
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
