"""API key security primitives.

Design (PRD FR-005, FR-011, FR-012):

- Secrets are generated with the OS CSPRNG: ``uaeg_`` + 43 url-safe base64
  characters (256 bits of entropy).
- The server stores ONLY a SHA-256 verifier of the secret, never the secret
  itself. The full secret is displayed exactly once at creation time.
- Key IDs are short, non-secret identifiers used in CLI/dashboard output.
- Verification hashes the presented bearer token and looks the verifier up;
  an attacker cannot recover secrets from the database.
"""

from __future__ import annotations

import hashlib
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
