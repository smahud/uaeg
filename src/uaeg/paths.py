"""Filesystem path resolution for UAEG.

Standard production layout (created by install.sh):

- Configuration:  /etc/uaeg/config.json        (UAEG_CONFIG overrides)
- Data directory: /var/lib/uaeg                (UAEG_DATA_DIR overrides)
    - uaeg.db                                 SQLite database
    - jobs/<job_id>/{stdout,stderr,exit}      background job artifacts
    - tls/self-signed.{crt,key}               generated self-signed certificate
    - sessions/                               session metadata artifacts

Environment overrides exist so tests and development instances can run
fully isolated from the system directories.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("/etc/uaeg/config.json")
DEFAULT_DATA_DIR = Path("/var/lib/uaeg")


def config_path() -> Path:
    """Return the config file path (env UAEG_CONFIG overrides)."""
    return Path(os.environ.get("UAEG_CONFIG", str(DEFAULT_CONFIG_PATH)))


def data_dir() -> Path:
    """Return the data directory (env UAEG_DATA_DIR overrides)."""
    return Path(os.environ.get("UAEG_DATA_DIR", str(DEFAULT_DATA_DIR)))


def database_path() -> Path:
    return data_dir() / "uaeg.db"


def jobs_dir() -> Path:
    return data_dir() / "jobs"


def sessions_dir() -> Path:
    return data_dir() / "sessions"


def tls_dir() -> Path:
    return data_dir() / "tls"


def ensure_data_dirs() -> None:
    """Create the data directory tree if missing (idempotent)."""
    for d in (data_dir(), jobs_dir(), sessions_dir(), tls_dir()):
        d.mkdir(parents=True, exist_ok=True)
