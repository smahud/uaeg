"""Regression tests: secret redaction in logs and persisted metadata.

Covers the P2 audit finding "audit-log exposure of secrets embedded in
command text": commands and output previews must be redacted before they
reach exec_logs, the jobs/sessions tables or the journal.
"""

from __future__ import annotations

from uaeg import security
from uaeg.db import Store


# ----------------------------------------------------------------------
# security.redact_secrets pattern coverage
# ----------------------------------------------------------------------
def test_redact_bearer_token_in_curl_header():
    cmd = 'curl -H "Authorization: Bearer eyJhbGciOi.very.secret.payload" https://api'
    out = security.redact_secrets(cmd)
    assert "Authorization: Bearer ***REDACTED***" in out
    assert "eyJhbGciOi" not in out
    assert "https://api" in out


def test_redact_uaeg_secret():
    secret = security.generate_secret()
    out = security.redact_secrets(f"echo {secret} | something")
    assert secret not in out
    assert "***REDACTED***" in out


def test_redact_password_in_url():
    out = security.redact_secrets("git clone https://alice:SuperSecret123@github.com/x/y.git")
    assert "SuperSecret123" not in out
    assert "https://alice:***REDACTED***@github.com/x/y.git" in out


def test_redact_generic_assignments():
    cases = [
        ("export password=hunter2rest", "hunter2rest"),
        ("--token=abcdef123456", "abcdef123456"),
        ("ENV api_key: qwerty123456", "qwerty123456"),
        ("aws_secret_access_key=wJalrXUtnFEMI1235EXAMPLE", "wJalrXUtnFEMI1235EXAMPLE"),
    ]
    for text, secret in cases:
        out = security.redact_secrets(text)
        assert secret not in out, f"secret survived redaction in: {text}"
        assert "***REDACTED***" in out


def test_redact_well_known_shapes():
    text = (
        "ghp_16CharactersSecretTok, sk-proj-1234567890abcdef, "
        "AKIAIOSFODNN7EXAMPLE, xoxb-1234567890abcde, "
        "AIzaSyA1234567890abcdefghijklmnopqrstuv"
    )
    out = security.redact_secrets(text)
    for fragment in (
        "ghp_16", "sk-proj-1234", "AKIAIOSFODNN7", "xoxb-1234", "AIzaSyA1",
    ):
        assert fragment not in out


def test_redact_private_key_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----"
    out = security.redact_secrets(f"cat <<'EOF'\n{pem}\nEOF")
    assert "MIIEow" not in out
    assert "<redacted private key>" in out


def test_redact_is_idempotent_and_safe_for_plain_commands():
    plain = "apt-get install -y jq && echo done; ls -la /var/log"
    assert security.redact_secrets(plain) == plain
    # double redaction does not corrupt the marker
    once = security.redact_secrets("token=abcdefgh1234")
    assert security.redact_secrets(once) == once


def test_redact_empty_and_none():
    assert security.redact_secrets("") == ""
    assert security.redact_secrets(None) == ""


# ----------------------------------------------------------------------
# persistence choke points
# ----------------------------------------------------------------------
def test_add_log_redacts_command_and_preview(tmp_path):
    store = Store(tmp_path / "uaeg.db")
    store.add_log(
        key_id="k-1", key_name="t", tool="shell_exec", status="ok",
        meta={"command": 'curl -H "Authorization: Bearer secrettoken123456" https://x'},
        output_preview="token=supersecretvalue output",
    )
    row = store.list_logs(limit=1)[0]
    assert "secrettoken123456" not in row["meta"]["command"]
    assert "Authorization: Bearer ***REDACTED***" in row["meta"]["command"]
    assert "supersecretvalue" not in (row["output_preview"] or "")
    store.close()


def test_insert_job_and_session_redact_command(tmp_path):
    store = Store(tmp_path / "uaeg.db")
    store.insert_job(
        "job-r1", "curl -H 'Authorization: Bearer tok1234567890' https://x",
        "/", 123, "k-1", "t",
    )
    store.insert_session(
        "sess-r1", "export password=hunter2xyz", "/", 456, "k-1", "t",
    )
    job = store.get_job("job-r1")
    assert "tok123456789" not in job["command"]
    assert "***REDACTED***" in job["command"]
    session = store.get_session("sess-r1")
    assert "hunter2xyz" not in session["command"]
    store.close()
