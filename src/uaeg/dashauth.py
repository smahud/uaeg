"""Dashboard authentication: password storage, sessions and login throttling.

Design (single-operator dashboard login):

- The dashboard password is stored ONLY as a salted scrypt hash
  (``hashlib.scrypt`` from the standard library - no new dependencies) in
  ``<data_dir>/dashboard-pass.json`` with 0600 permissions. The plaintext
  password is never persisted, logged or echoed.
- The file carries a random ``generation`` token. Server-side sessions are
  bound to the generation they were created under; writing a new password
  (new generation) instantly revokes every existing session WITHOUT a
  service restart, because the server re-reads the file when its mtime
  changes.
- Writes are atomic (tmp file + fsync + ``os.replace`` + directory fsync):
  a crashed ``uaeg pass`` can never leave a truncated or missing file where
  a valid one existed.
- The password is a SEPARATE credential from the MCP agent keys: nothing
  in this module touches the ``keys`` table, and nothing in the MCP bearer
  path touches this file.
- Sessions are kept server-side in memory (fully revocable, nothing in
  localStorage / bearer material on the client). The browser only holds an
  opaque ``HttpOnly`` session cookie.
- ``LoginThrottle`` implements per-source-IP sliding-window lockout to
  blunt brute-force attacks against a dashboard exposed to the internet.

Backward compatibility: when NO password file exists, the dashboard keeps
behaving exactly like UAEG v0.2.0 on a private bind (no login). Once a
password exists, login is enforced on every bind address. A wildcard
(public) bind additionally REQUIRES a password and TLS - enforced by
``server.serve()`` and ``Config.validate()`` (fail closed).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths

log = logging.getLogger("uaeg.dashauth")

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
MIN_PASSWORD_LEN = 12
MAX_PASSWORD_LEN = 1024
PASSWORD_FILENAME = "dashboard-pass.json"

SESSION_COOKIE = "uaeg_session"

# scrypt parameters (memory-hard, ~32 MiB per hash, ~80 ms on a small VPS).
SCRYPT_N = 32768
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32

# Login brute-force protection (per source IP).
MAX_FAILED_ATTEMPTS = 5
FAILURE_WINDOW_S = 15 * 60
LOCKOUT_S = 15 * 60

# Request body limits.
MAX_LOGIN_BODY_BYTES = 64 * 1024
MAX_BODY_BYTES = 1024 * 1024


class PasswordFileError(Exception):
    """The password file exists but cannot be parsed."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def password_file_path() -> Path:
    return paths.data_dir() / PASSWORD_FILENAME


# ----------------------------------------------------------------------
# Password record: hashing, (de)serialization, atomic persistence
# ----------------------------------------------------------------------
@dataclass
class PasswordRecord:
    scheme: str
    n: int
    r: int
    p: int
    salt_hex: str
    hash_hex: str
    generation: str
    created_at: str
    updated_at: str


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    # maxmem must cover 128 * r * (n + p + 2) bytes; use a generous bound
    # so records written by other UAEG versions still verify.
    maxmem = max(64 * 1024 * 1024, 128 * r * (n + p + 2) * 2)
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=SCRYPT_DKLEN,
        maxmem=maxmem,
    )


def hash_password(password: str) -> PasswordRecord:
    """Hash a password with a fresh random salt (scrypt)."""
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    now = _utcnow()
    return PasswordRecord(
        scheme="scrypt",
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        salt_hex=salt.hex(),
        hash_hex=digest.hex(),
        generation=secrets.token_hex(16),
        created_at=now,
        updated_at=now,
    )


def verify_password(password: str, record: PasswordRecord) -> bool:
    """Constant-time verification of a password against a stored record."""
    try:
        salt = bytes.fromhex(record.salt_hex)
        digest = _scrypt(password, salt, record.n, record.r, record.p)
    except (ValueError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(digest.hex(), record.hash_hex)


def save_password(record: PasswordRecord) -> None:
    """Atomically persist a password record (0600, tmp+fsync+replace).

    Either the previous file survives untouched or the new one is fully in
    place - a crash mid-write can never destroy the stored credential.
    Raises OSError (incl. PermissionError) so callers can report cleanly.
    """
    target = password_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    payload = json.dumps(asdict(record), indent=2) + "\n"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    # Make the rename itself durable.
    try:
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass  # directory fsync is best-effort on some filesystems


def load_password() -> PasswordRecord | None:
    """Load the stored record; None when no password is configured.

    Raises PasswordFileError when the file exists but is corrupt - callers
    must treat that as "configured but broken" (fail closed), never as
    "not configured" (which would silently disable login).
    """
    path = password_file_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PasswordFileError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
        record = PasswordRecord(
            scheme=str(data["scheme"]),
            n=int(data["n"]),
            r=int(data["r"]),
            p=int(data["p"]),
            salt_hex=str(data["salt_hex"]),
            hash_hex=str(data["hash_hex"]),
            generation=str(data["generation"]),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise PasswordFileError(
            f"{path} is corrupt or not a UAEG password file ({exc}). "
            "Run 'sudo uaeg pass' to rewrite it."
        ) from exc
    if record.scheme != "scrypt" or not record.salt_hex or not record.hash_hex:
        raise PasswordFileError(
            f"{path} uses an unsupported scheme ({record.scheme!r}). "
            "Run 'sudo uaeg pass' to rewrite it."
        )
    return record


def password_is_configured() -> bool:
    """True when a password file exists (even if corrupt - fail closed)."""
    try:
        return password_file_path().is_file()
    except OSError:
        return False


# ----------------------------------------------------------------------
# PasswordManager: hot-path access with mtime-based caching
# ----------------------------------------------------------------------
class PasswordManager:
    """Server-side view of the password file.

    The file is re-read whenever its mtime changes (or every 2 s as a
    safety net), so ``uaeg pass`` takes effect immediately, without a
    service restart. A corrupt file keeps the LAST GOOD record AND marks
    the state broken - login then always fails closed.
    """

    _RECHECK_S = 2.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._record: PasswordRecord | None = None
        self._mtime_ns: int | None = None
        self._broken = False
        self._last_check = 0.0

    def _refresh(self) -> None:
        path = password_file_path()
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = None

        now = time.monotonic()
        with self._lock:
            if (
                mtime_ns == self._mtime_ns
                and not self._broken
                and (now - self._last_check) < self._RECHECK_S
            ):
                return  # unchanged within the TTL

        record: PasswordRecord | None = None
        broken = False
        if mtime_ns is not None:
            try:
                record = load_password()
            except PasswordFileError as exc:
                broken = True
                record = None
                log.error("dashboard password file is unreadable: %s", exc)
        with self._lock:
            self._mtime_ns = mtime_ns
            self._record = record
            self._broken = broken
            self._last_check = now

    def is_configured(self) -> bool:
        """True when a password is REQUIRED (file present, even if broken)."""
        self._refresh()
        with self._lock:
            return self._record is not None or self._broken

    def is_broken(self) -> bool:
        self._refresh()
        with self._lock:
            return self._broken

    def generation(self) -> str | None:
        self._refresh()
        with self._lock:
            return self._record.generation if self._record else None

    def verify(self, password: str) -> bool:
        """Verify a candidate password against the current record."""
        self._refresh()
        with self._lock:
            record, broken = self._record, self._broken
        if broken or record is None:
            return False
        return verify_password(password, record)


# ----------------------------------------------------------------------
# SessionStore: server-side, revocable, generation-bound sessions
# ----------------------------------------------------------------------
@dataclass
class Session:
    token: str
    csrf_token: str
    generation: str
    created_at: float
    last_seen: float

    @property
    def expires_at(self) -> float:
        return self.last_seen  # enforced against idle timeout by the store


class SessionStore:
    """Opaque-token sessions held in process memory.

    - Tokens are 256-bit random values; the client never chooses one
      (session-fixation impossible).
    - A session is only valid while its ``generation`` matches the CURRENT
      password generation -> changing the password revokes everything.
    - Idle and absolute timeouts are enforced on every lookup.
    """

    def __init__(self, idle_timeout_s: float, absolute_timeout_s: float):
        # Config validation enforces sane operator-facing minimums (>=60 s);
        # the store itself only requires positive, ordered values so tests
        # can exercise expiry quickly.
        self.idle_timeout_s: float = max(0.001, float(idle_timeout_s))
        self.absolute_timeout_s: float = max(
            self.idle_timeout_s, float(absolute_timeout_s)
        )
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, generation: str) -> Session:
        now = time.time()
        session = Session(
            token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            generation=generation,
            created_at=now,
            last_seen=now,
        )
        with self._lock:
            self._prune_locked(now)
            self._sessions[session.token] = session
        return session

    def get_valid(self, token: str | None, generation: str | None) -> Session | None:
        if not token or generation is None:
            return None
        now = time.time()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if (
                session.generation != generation
                or now - session.last_seen > self.idle_timeout_s
                or now - session.created_at > self.absolute_timeout_s
            ):
                self._sessions.pop(token, None)
                return None
            session.last_seen = now
            return session

    def revoke(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            return self._sessions.pop(token, None) is not None

    def revoke_all(self) -> int:
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            return count

    def _prune_locked(self, now: float) -> None:
        # Called under lock from create(); keeps memory bounded.
        stale = [
            t
            for t, s in self._sessions.items()
            if now - s.last_seen > self.idle_timeout_s
            or now - s.created_at > self.absolute_timeout_s
        ]
        for t in stale:
            self._sessions.pop(t, None)


# ----------------------------------------------------------------------
# LoginThrottle: per-IP sliding window + temporary lockout
# ----------------------------------------------------------------------
class LoginThrottle:
    """Per-source-IP brute-force protection.

    After MAX_FAILED_ATTEMPTS failures within FAILURE_WINDOW_S the source
    IP is locked out for LOCKOUT_S. Successful logins clear that IP's
    failure history. Deliberately per-IP (never global) so a third party
    cannot lock the legitimate operator out of their own dashboard.
    """

    def __init__(
        self,
        max_failures: int = MAX_FAILED_ATTEMPTS,
        window_s: float = FAILURE_WINDOW_S,
        lockout_s: float = LOCKOUT_S,
    ):
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def locked_for(self, ip: str) -> int:
        """Seconds of lockout remaining for ``ip`` (0 = not locked)."""
        now = time.time()
        with self._lock:
            until = self._locked_until.get(ip)
            if until is None:
                return 0
            if until <= now:
                self._locked_until.pop(ip, None)
                return 0
            return int(until - now) + 1

    def record_failure(self, ip: str) -> bool:
        """Record a failed attempt; True when this failure TRIGGERED a lockout."""
        now = time.time()
        with self._lock:
            events = [t for t in self._failures.get(ip, []) if t > now - self.window_s]
            events.append(now)
            self._failures[ip] = events
            if len(self._failures[ip]) >= self.max_failures:
                self._locked_until[ip] = now + self.lockout_s
                self._failures[ip] = []
                self._prune(now)
                return True
            self._prune(now)
            return False

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)

    def _prune(self, now: float) -> None:
        # Keep the bookkeeping dicts small even under address-spray attacks.
        if len(self._failures) > 4096:
            cutoff = now - self.window_s
            self._failures = {
                ip: evs
                for ip, evs in self._failures.items()
                if evs and evs[-1] > cutoff
            }
        if len(self._locked_until) > 4096:
            self._locked_until = {
                ip: until for ip, until in self._locked_until.items() if until > now
            }
