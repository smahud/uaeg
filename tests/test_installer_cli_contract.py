"""Installer CLI contract regression tests (audit 2026-11-09).

Incident class: ``report/PRODUCTION_RECOVERY_V041.md`` (revision 1)
documented an installer invocation with flags that do not exist
(``--tls-mode``, ``--cert-file``, ``--key-file``).  The operator's repair
died with ``ERROR: unknown option: --tls-mode`` before any mutation.

This suite locks the contract on BOTH sides:

1. **Parser side** (``install.sh``):
   - canonical TLS flags accepted (``--domain`` | ``--cert FILE --key FILE``
     | ``--self-signed`` — exactly one source per invocation);
   - unsupported flags rejected BEFORE any mutation (the failure must
     happen at parse time, before the root check: no sudo exec, no apt,
     no service stop, no file write);
   - missing option values rejected with an actionable message;
   - ``--cert`` without ``--key`` (and vice versa) rejected pre-mutation;
   - missing manual TLS files rejected pre-mutation;
   - conflicting TLS sources rejected (previously last-one-won silently);
   - ``--help`` is accurate and free of duplicated blocks.

2. **Documentation side**: every ``install.sh`` flag used inside bash code
   blocks of the live operator docs (docs/*.md, README.md, the production
   recovery runbook) must exist in the parser, and the runbook must not
   invoke the non-existent TLS flags in any code block.

The e2e counterpart (a full in-place ``--repair`` with manual TLS + public
dashboard against an isolated installation) lives in
``tests/test_e2e_installer_repair.py``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "install.sh"

# The non-existent flags that caused the 2026-11-09 incident (runbook
# revision 1). They must never appear in an executable code block again.
GHOST_FLAGS = ("--tls-mode", "--cert-file", "--key-file")

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _clean(text: str) -> str:
    """Strip ANSI colors and collapse whitespace (installer messages wrap)."""
    return re.sub(r"\s+", " ", _ANSI.sub("", text))


def run_installer(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the UNPATCHED installer; parse-level failures happen before the
    root check, so this is safe as a non-root user."""
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(cwd or REPO),
    )


@pytest.fixture(scope="module")
def patched_installer(tmp_path_factory) -> Path:
    """Root-check-disabled copy of the installer (same patch the CI
    ``installer`` job and the upgrade-contract suite apply), for the
    canonical-acceptance (``--dry-run``) test only."""
    base = tmp_path_factory.mktemp("installer-cli")
    patched = base / "install-test.sh"
    sed = subprocess.run(
        [
            "sed",
            "-e", r"s|^if \[\[ \$EUID -ne 0 \]\]; then|if false; then|",
            "-e", r's|^  die "this installer must run as root.*|  :|',
            "-e", r"s|^apt-get update -qq$|:|",
            "-e", r"s|^apt-get install -y -qq python3 python3-venv openssl curl >/dev/null$|:|",
            str(INSTALLER),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    patched.write_text(sed.stdout)
    return patched


@pytest.fixture(scope="module")
def tls_pair(tmp_path_factory) -> tuple[Path, Path]:
    """A real, parseable certificate/key pair for manual-TLS tests."""
    base = tmp_path_factory.mktemp("tls")
    cert, key = base / "manual.crt", base / "manual.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
            "-days", "5", "-nodes", "-subj", "/CN=host.zerotier.my.id",
            "-addext", "subjectAltName=DNS:host.zerotier.my.id,IP:127.0.0.1",
            "-keyout", str(key), "-out", str(cert),
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


# ----------------------------------------------------------------------
# The incident, locked: unsupported flags die at parse time, pre-mutation
# ----------------------------------------------------------------------
def test_incident_unknown_tls_mode_flag_rejected_before_mutation(tmp_path):
    """The EXACT operator invocation from the incident must fail cleanly
    at argument parsing — before privilege escalation, apt, service
    control or any file write (no root check reached)."""
    proc = run_installer(
        "--repair",
        "--dashboard-bind", "public",
        "--tls-mode", "manual",
        "--cert-file", "/etc/letsencrypt/live/host.zerotier.my.id/fullchain.pem",
        "--key-file", "/etc/letsencrypt/live/host.zerotier.my.id/privkey.pem",
        cwd=tmp_path,
    )
    assert proc.returncode != 0
    assert "unknown option: --tls-mode" in proc.stderr
    # Parse-time rejection: the root check must NOT have been reached.
    assert "must run as root" not in proc.stderr
    assert "sudo" not in proc.stderr
    # Nothing was mutated: no files in the working directory, no bootstrap
    # scratch dirs.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("ghost", GHOST_FLAGS)
def test_ghost_flags_are_not_options(tmp_path, ghost):
    proc = run_installer(ghost, "manual", cwd=tmp_path)
    assert proc.returncode != 0
    assert "unknown option" in proc.stderr
    assert "must run as root" not in proc.stderr


def test_help_advertises_canonical_tls_interface():
    proc = run_installer("--help")
    assert proc.returncode == 0
    help_text = proc.stdout
    assert "--cert FILE --key FILE" in help_text
    assert "--domain DOMAIN" in help_text
    assert "--self-signed" in help_text
    for ghost in GHOST_FLAGS:
        assert ghost not in help_text, f"--help must not mention {ghost}"
    # Regression for the duplicated --repair paragraph (v0.4.1 help bug):
    # no help line may appear twice in the whole output.
    lines = [ln.rstrip() for ln in help_text.splitlines()]
    seen: set[str] = set()
    for line in lines:
        if not line.strip():
            continue
        assert line not in seen, f"duplicated help line: {line!r}"
        seen.add(line)


# ----------------------------------------------------------------------
# Missing values / incomplete pairs / bad paths — pre-mutation rejections
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "option", ["--port", "--dashboard-port", "--dashboard-bind", "--domain",
               "--public-ip", "--cert", "--key", "--data-dir", "--config-dir",
               "--install-dir"],
)
def test_missing_option_value_rejected_with_actionable_error(tmp_path, option):
    proc = run_installer(option, cwd=tmp_path)
    assert proc.returncode != 0
    assert f"option {option} requires a value" in proc.stderr
    assert "must run as root" not in proc.stderr


def test_option_like_value_rejected(tmp_path):
    proc = run_installer("--cert", "--key", "x.pem", cwd=tmp_path)
    assert proc.returncode != 0
    assert "option --cert requires a value" in proc.stderr


@pytest.mark.parametrize("args", [["--cert", "/tmp/x.pem"], ["--key", "/tmp/y.pem"]])
def test_manual_tls_requires_the_complete_pair(tmp_path, args):
    proc = run_installer(*args, cwd=tmp_path)
    assert proc.returncode != 0
    assert "manual TLS requires BOTH --cert FILE and --key FILE" in proc.stderr
    assert "must run as root" not in proc.stderr


def test_manual_tls_rejects_missing_files_before_mutation(tmp_path):
    proc = run_installer("--cert", str(tmp_path / "nope.crt"),
                         "--key", str(tmp_path / "nope.key"), cwd=tmp_path)
    assert proc.returncode != 0
    assert "certificate file not found" in proc.stderr
    assert "must run as root" not in proc.stderr


# ----------------------------------------------------------------------
# Conflicting TLS sources — exactly one per invocation
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "args",
    [
        ["--domain", "gw.example.com", "--cert", "C", "--key", "K"],   # certbot+manual
        ["--cert", "C", "--key", "K", "--domain", "gw.example.com"],   # order-independent
        ["--self-signed", "--cert", "C", "--key", "K"],                # selfsigned+manual
        ["--domain", "gw.example.com", "--self-signed"],               # certbot+selfsigned
    ],
)
def test_conflicting_tls_sources_rejected(tmp_path, args):
    # Give real file paths so the conflict check is what fires for the
    # manual variants (existence is checked after the conflict check).
    args = [
        "C" if a == "C" else ("K" if a == "K" else a) for a in args
    ]
    proc = run_installer(*args, cwd=tmp_path)
    assert proc.returncode != 0
    assert "conflicting TLS options" in proc.stderr
    assert "must run as root" not in proc.stderr


# ----------------------------------------------------------------------
# Canonical acceptance: the corrected recovery invocation parses and plans
# ----------------------------------------------------------------------
def test_canonical_recovery_invocation_accepted(patched_installer, tls_pair):
    """The CORRECTED runbook invocation (revision 2) parses and produces
    the expected plan — manual TLS, public dashboard, default ports."""
    cert, key = tls_pair
    proc = subprocess.run(
        [
            "bash", str(patched_installer), "--dry-run",
            "--dashboard-bind", "public",
            "--cert", str(cert),
            "--key", str(key),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO),  # a checkout (pyproject.toml present): no download
    )
    assert proc.returncode == 0, proc.stderr
    assert "TLS mode       : manual" in proc.stdout
    assert "dashboard      : public:3903" in proc.stdout
    assert "MCP port       : 3902" in proc.stdout


def test_canonical_flags_with_explicit_ports_accepted(patched_installer, tls_pair):
    cert, key = tls_pair
    proc = subprocess.run(
        [
            "bash", str(patched_installer), "--dry-run",
            "--port", "3902", "--dashboard-port", "3903",
            "--dashboard-bind", "public",
            "--cert", str(cert), "--key", str(key),
            "--no-service",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr
    assert "public:3903" in proc.stdout
    assert "--no-service" in proc.stdout


def test_config_flags_with_service_command_rejected(tmp_path):
    proc = run_installer("--status", "--port", "3902", cwd=tmp_path)
    assert proc.returncode != 0
    assert "apply to the install flow" in _clean(proc.stderr)
    assert "must run as root" not in _clean(proc.stderr)


# ----------------------------------------------------------------------
# Documentation contract: docs must use the parser's real flags
# ----------------------------------------------------------------------
def _parser_options() -> set[str]:
    """Ground truth: the long options the case-statement actually accepts
    (covers the plain, ``--yes|-y`` and ``-h|--help`` arm forms)."""
    source = INSTALLER.read_text(encoding="utf-8")
    return set(
        re.findall(r"^    (?:-[a-z]\|)?(--[a-z][a-z0-9-]*)(?:\|-[a-z])?\)", source, re.M)
    )


def _bash_blocks(markdown: str, skip_comments: bool = True) -> list[str]:
    blocks: list[str] = []
    inside = False
    for line in markdown.splitlines():
        if line.strip().startswith("```bash"):
            inside = True
            continue
        if inside and line.strip().startswith("```"):
            inside = False
            continue
        if inside:
            if skip_comments and line.lstrip().startswith("#"):
                continue
            blocks.append(line)
    return blocks


def _flags_on_install_sh_lines(bash_lines: list[str]) -> set[str]:
    """Long options used on (continuation-joined) command lines that invoke
    install.sh — 'uninstall.sh' does NOT count (word boundary)."""
    joined: list[str] = []
    buf = ""
    for line in bash_lines:
        text = line.lstrip()
        if text.startswith("#"):
            continue  # comments
        if buf:
            buf += " " + text
        else:
            buf = text
        if buf.rstrip().endswith("\\"):
            buf = buf.rstrip()[:-1]
            continue
        joined.append(buf)
        buf = ""
    if buf:
        joined.append(buf)
    flags: set[str] = set()
    for line in joined:
        if not re.search(r"(?<![A-Za-z])install\.sh(?![A-Za-z])", line):
            continue
        flags.update(re.findall(r"(?<![\w/-])(--[a-z][a-z0-9-]*)", line))
    return flags


LIVE_DOCS = sorted(
    [REPO / "README.md", *sorted((REPO / "docs").glob("*.md")),
     REPO / "report" / "PRODUCTION_RECOVERY_V041.md"]
)


def test_parser_exposes_canonical_option_set():
    """Sanity: the option set extracted from the parser is the expected
    canonical interface (guards the extraction itself)."""
    options = _parser_options()
    expected = {
        "--start", "--stop", "--restart", "--status", "--check", "--dry-run",
        "--rollback", "--repair", "--port", "--dashboard-port",
        "--dashboard-bind", "--domain", "--public-ip", "--cert", "--key",
        "--self-signed", "--data-dir", "--config-dir", "--install-dir",
        "--no-service", "--open-firewall", "--yes", "--version", "--help",
    }
    assert expected <= options, f"missing: {expected - options}"
    assert "--tls-mode" not in options
    assert "--cert-file" not in options
    assert "--key-file" not in options


def test_live_docs_use_only_real_installer_flags():
    """Every install.sh flag invoked in the live operator documentation
    must exist in the parser — the doc/CLI mismatch that caused this
    incident can no longer regress silently."""
    options = _parser_options()
    for doc in LIVE_DOCS:
        assert doc.is_file(), doc
        used = _flags_on_install_sh_lines(_bash_blocks(doc.read_text(encoding="utf-8")))
        unknown = used - options
        assert not unknown, f"{doc.name} invokes unknown installer flags: {sorted(unknown)}"


def test_recovery_runbook_never_invokes_ghost_flags():
    """The runbook must not invoke --tls-mode/--cert-file/--key-file in any
    executable code block (they may appear in prose explaining the errata)."""
    runbook = (REPO / "report" / "PRODUCTION_RECOVERY_V041.md").read_text(encoding="utf-8")
    for ghost in GHOST_FLAGS:
        for line in _bash_blocks(runbook):
            assert ghost not in line, f"runbook code block invokes {ghost}: {line!r}"


def test_recovery_runbook_documents_canonical_invocation():
    """The corrected repair command (canonical flags) is present verbatim."""
    runbook = (REPO / "report" / "PRODUCTION_RECOVERY_V041.md").read_text(encoding="utf-8")
    assert "bash install.sh --repair" in runbook
    assert "--cert /etc/letsencrypt/live/host.zerotier.my.id/fullchain.pem" in runbook
    assert "--key  /etc/letsencrypt/live/host.zerotier.my.id/privkey.pem" in runbook


def test_recovery_runbook_manual_alternative_orders_bind_last():
    """D4 regression lock: the `uaeg config set` sequence must set
    dashboard.bind LAST (each set validates the whole config)."""
    runbook = (REPO / "report" / "PRODUCTION_RECOVERY_V041.md").read_text(encoding="utf-8")
    seq = [
        m.group(1)
        for m in re.finditer(
            r"uaeg config set (dashboard\.\w+)", runbook
        )
    ]
    assert seq, "runbook must document the uaeg config set alternative"
    assert seq.index("dashboard.bind") == len(seq) - 1, (
        f"dashboard.bind must be set LAST (got order {seq})"
    )


# ----------------------------------------------------------------------
# upgrade.sh CLI contract (audit round 2, 2026-11-09)
#
# install.sh's downgrade-refusal messages direct operators to
# ``sudo bash upgrade.sh --version X.Y.Z --allow-downgrade``, and
# docs/INSTALL.md / docs/TROUBLESHOOTING.md document ``--version X.Y.Z``
# and ``--source DIR``. A missing value used to kill the script with
# bash's raw ``unbound variable`` (the D6 class round 1 fixed in
# install.sh; defect D11 in round 2's numbering). It was always a SAFE,
# pre-mutation failure - but not an actionable one.
# ----------------------------------------------------------------------
UPGRADE_SH = REPO / "upgrade.sh"


def run_upgrade_sh(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the real upgrade.sh. Value errors die at parse time - before
    the root check, before any file write and before any network use."""
    return subprocess.run(
        ["bash", str(UPGRADE_SH), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(cwd or REPO),
    )


@pytest.fixture(scope="module")
def patched_upgrade_sh(tmp_path_factory) -> Path:
    """Root-check-disabled copy of upgrade.sh (the same sed patch the CI
    installer job applies to install.sh), used ONLY to prove a COMPLETE
    ``--version X.Y.Z`` passes the parser: --bootstrap-info exits before
    any network or updater activity, so the positive path is provable
    without side effects."""
    base = tmp_path_factory.mktemp("upgrade-cli")
    patched = base / "upgrade-test.sh"
    sed = subprocess.run(
        [
            "sed",
            "-e", r"s|^if \[\[ \$EUID -ne 0 \]\]; then|if false; then|",
            "-e", r's|^  die "this script must run as root.*|  :|',
            str(UPGRADE_SH),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    patched.write_text(sed.stdout)
    return patched


@pytest.mark.parametrize("option", ["--version", "--source"])
def test_upgrade_sh_missing_value_rejected_with_actionable_error(tmp_path, option):
    proc = run_upgrade_sh(option, cwd=tmp_path)
    assert proc.returncode != 0
    assert f"option {option} requires a value" in proc.stderr
    # Not bash's raw failure, and the death happened at parse time: the
    # root gate was never reached and nothing was created or fetched.
    assert "unbound variable" not in proc.stderr
    assert "must run as root" not in proc.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("option", ["--version", "--source"])
def test_upgrade_sh_option_like_value_rejected(tmp_path, option):
    proc = run_upgrade_sh(option, "--check", cwd=tmp_path)
    assert proc.returncode != 0
    assert f"option {option} requires a value" in proc.stderr
    assert "unbound variable" not in proc.stderr
    assert "must run as root" not in proc.stderr


def test_upgrade_sh_complete_version_pair_passes_the_parser(patched_upgrade_sh):
    """A complete `--version X.Y.Z` gets PAST the parser: with the root
    gate disabled, --bootstrap-info prints its info block and exits 0
    BEFORE any network or updater activity - proving the guard fires only
    on genuinely missing values, never on valid invocations."""
    proc = subprocess.run(
        ["bash", str(patched_upgrade_sh), "--version", "0.4.1", "--bootstrap-info"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr
    assert "requires a value" not in proc.stderr
    assert "unbound variable" not in proc.stderr
    assert "UAEG upgrade bootstrap" in proc.stdout


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
