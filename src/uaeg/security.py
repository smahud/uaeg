"""API key security primitives.

Design (PRD FR-005, FR-011, FR-012):

- Secrets are generated with the OS CSPRNG: ``uaeg_`` + 43 url-safe base64
  characters (256 bits of entropy).
- The server stores ONLY a SHA-256 verifier of the secret, never the secret
  itself. The full secret is displayed exactly once at creation time.
- Key IDs are short, non-secret identifiers used in CLI/dashboard output.
- Verification hashes the presented bearer token and looks the verifier up;
  an attacker cannot recover secrets from the database.

Secret redaction (PRD section 13: "No sensitive credentials in ordinary
logs"): commands executed by agents may legitimately embed third-party
credentials (curl headers, git remotes, export lines). ``redact_secrets``
masks high-confidence secret shapes before command text or output previews
are written to the audit log, job/session metadata or the service journal.
"""

from __future__ import annotations

import hashlib
import re
import secrets

SECRET_PREFIX = "uaeg_"


class KeyPair:
    """A newly generated key: secret shown once + its persisted metadata."""

    __slots__ = ("key_id", "name", "secret", "prefix", "verifier")

    def __init__(self, key_id: str, name: str, secret: str, prefix: str, verifier: str):
        self.key_id = key_id
        self.name = name
        self.secret = secret
        self.prefix = prefix
        self.verifier = verifier


def generate_secret() -> str:
    """Generate a new agent secret (256 bits of entropy, url-safe)."""
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def generate_key_id() -> str:
    """Generate a short public key identifier (non-secret)."""
    return "k-" + secrets.token_hex(6)


def secret_prefix(secret: str, visible: int = 12) -> str:
    """Return the displayable prefix of a secret (e.g. uaeg_Ab3xYz9Q)."""
    return secret[:visible]


def verifier(secret: str) -> str:
    """Return the SHA-256 hex digest stored server-side (non-recoverable)."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def new_key(name: str) -> KeyPair:
    """Generate a complete new key material set."""
    secret = generate_secret()
    return KeyPair(
        key_id=generate_key_id(),
        name=name,
        secret=secret,
        prefix=secret_prefix(secret),
        verifier=verifier(secret),
    )


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ----------------------------------------------------------------------
# Secret redaction for logs and persisted command metadata
# ----------------------------------------------------------------------
# High-confidence secret shapes only: these patterns are specific enough
# that ordinary shell commands do not trigger them by accident. Redaction
# deliberately errs toward masking (documented in docs/SECURITY.md).
_REDACT_MARK = "***REDACTED***"


def _redact_auth_header(m: "re.Match[str]") -> str:
    return f"{m.group(1)}: {m.group(2)} {_REDACT_MARK}"


def _redact_url_password(m: "re.Match[str]") -> str:
    return f"{m.group(1)}:{_REDACT_MARK}@"


def _redact_assignment(m: "re.Match[str]") -> str:
    return f"{m.group(1)}{m.group(2)}{_REDACT_MARK}"


_REDACTION_RULES: list[tuple[re.Pattern[str], str | "re.Match[str]"]] = [
    # PEM private key blocks (multiline) - mask the whole block
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "<redacted private key>",
    ),
    # Authorization-style headers/assignments: keep the scheme word, mask value
    (
        re.compile(
            r"(?i)\b(authorization|proxy-authorization|x-api-key|auth)\b['\"]?\s*[:=]\s*['\"]?"
            r"(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"
        ),
        _redact_auth_header,
    ),
    # Credential-bearing URLs: scheme://user:password@host -> mask password
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/@\s'\"]+):([^@/\s'\"]{3,})@"),
        _redact_url_password,
    ),
    # Well-known token shapes
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), _REDACT_MARK),
    (re.compile(r"\buaeg_[A-Za-z0-9_-]{16,}\b"), _REDACT_MARK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), _REDACT_MARK),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), _REDACT_MARK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _REDACT_MARK),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), _REDACT_MARK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), _REDACT_MARK),
    # Generic secret-bearing assignments: password=..., api_key=...,
    # aws_secret_access_key=... - keep the name, mask the value
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key|"
            r"client[_-]?secret|aws_secret_access_key)\b['\"]?\s*([:=])\s*['\"]?"
            r"([^\s'\"&;|]{6,})"
        ),
        _redact_assignment,
    ),
]


def redact_secrets(text: str | None) -> str:
    """Mask high-confidence secrets embedded in ``text`` (best effort).

    Applied to command text, output previews and journal log lines before
    they are persisted or emitted. The raw output of a command is never
    altered - only what UAEG writes into its own logs and metadata.
    """
    if not text:
        return text or ""
    out = text
    for pattern, replacement in _REDACTION_RULES:
        out = pattern.sub(replacement, out)
    return out

