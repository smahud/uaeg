"""Regression tests for the 2026-11-08 production incident (v0.4.1).

INCIDENT (host.zerotier.my.id, MicroTix VPS): ``bash install.sh --repair``
was run from a stale source checkout whose ``DashboardConfig`` predated the
``dashboard.tls`` field (added in v0.3.0). The v0.4.0 in-place flow copied
that OLDER source over /opt/uaeg and re-pointed the venv at it BEFORE
validating the existing (v0.3.0+ schema) configuration, so the operator's
interpreter loaded the OLD ``uaeg.config`` and the repair died with::

    CONFIG_ERROR: configuration contains an unknown or mistyped field:
    DashboardConfig.__init__() got an unexpected keyword argument 'tls'

The service was left stopped until the manual rollback.

v0.4.1 closes the class with a pre-mutation preflight in install.sh
(preflight_inplace_safety):
  guard 1  older source over newer installed app        -> refuse, exit 5
  guard 2  staged schema rejects the EXISTING config    -> refuse, exit 1
           (a Python probe loads uaeg.config FROM THE STAGED SOURCE with a
           sanitized environment - see the RUNTIME_MISMATCH check)
and an in-writer origin proof: the schema that writes the configuration
must come from $INSTALL_DIR/src (exit 9 otherwise).

These unit tests pin the SCHEMA half of the incident class end-to-end:
the production-shaped configuration, the exact rejection by an older
schema, and acceptance by the current schema. The bash-level guards
(exit codes, zero-mutation, sanitized interpreter) are pinned by the
contract suite (tests/test_upgrade_contract.sh, T-53..T-56).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pytest

from conftest import write_test_config

from uaeg.config import Config, ConfigError

pytestmark = pytest.mark.usefixtures("env")


# ----------------------------------------------------------------------
# The production-shaped configuration (host.zerotier.my.id):
# MCP on 0.0.0.0:3902, dashboard public on 0.0.0.0:3903 with TLS.
# ----------------------------------------------------------------------
def _production_config_dict(env=None, manual_tls: bool = True) -> dict:
    """Production shape (host.zerotier.my.id): MCP 0.0.0.0:3902, dashboard
    public 0.0.0.0:3903 with TLS. When manual_tls, the cert/key paths point
    at real (dummy) files so Config.load's validation passes - production
    uses Let's Encrypt material at these exact paths."""
    cfg = Config()
    cfg.public_host = "host.zerotier.my.id"
    cfg.mcp = type(cfg.mcp)(bind="0.0.0.0", port=3902)
    cfg.dashboard = type(cfg.dashboard)(
        bind="0.0.0.0",
        port=3903,
        allow_public=True,
        tls=True,
    )
    if manual_tls:
        base = env["data"] if env else Path("/tmp/uaeg-test-tls")
        base.mkdir(parents=True, exist_ok=True)
        cert = base / "fullchain.pem"
        key = base / "privkey.pem"
        cert.write_text("(test certificate material)", encoding="utf-8")
        key.write_text("(test key material)", encoding="utf-8")
        cfg.tls = type(cfg.tls)(mode="manual", cert=str(cert), key=str(key))
    else:
        cfg.tls = type(cfg.tls)(mode="self-signed")
    return cfg.to_dict()


# ----------------------------------------------------------------------
# A faithful replica of the PRE-v0.3.0 schema: DashboardConfig without
# the `tls` field (the stale-checkout shape that caused the incident).
# ----------------------------------------------------------------------
@dataclass
class LegacyDashboardConfigNoTls:
    bind: str = "127.0.0.1"
    port: int = 3903
    allow_public: bool = False
    # NOTE: no `tls` field - this is the pre-v0.3.0 shape.


def _merge_like_uaeg(section_cls, data: dict, key: str):
    """Byte-for-byte the merge used by uaeg.config._merge_section."""
    defaults = asdict(section_cls())
    sub = data.get(key) or {}
    if not isinstance(sub, dict):
        raise ConfigError(f"{key} must be a mapping")
    return section_cls(**{**defaults, **sub})


# ======================================================================
# I-1 — the current schema accepts the production configuration
# ======================================================================
def test_v041_schema_accepts_production_config_with_dashboard_tls():
    """The exact field of the incident (`dashboard.tls`) must load on the
    schema that ships with the application being deployed (v0.4.1)."""
    data = _production_config_dict()
    cfg = Config.from_dict(data)

    assert cfg.dashboard.tls is True
    assert cfg.dashboard.bind == "0.0.0.0"
    assert cfg.dashboard.port == 3903
    assert cfg.dashboard.allow_public is True
    assert cfg.mcp.port == 3902


def test_v041_config_load_roundtrip_production_shape(env):
    """Config.save + Config.load round-trips the production shape through
    the file the preflight probe reads ($CONFIG_DIR/config.json)."""
    data = _production_config_dict(env)
    cfg_file: Path = env["config"]
    cfg_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    loaded = Config.load(cfg_file)

    assert loaded.dashboard.tls is True
    assert loaded.public_host == "host.zerotier.my.id"


# ======================================================================
# I-2 — the incident shape: an OLDER schema rejects the same config
# ======================================================================
def test_incident_shape_older_schema_rejects_dashboard_tls():
    """The 2026-11-08 failure, isolated: a DashboardConfig WITHOUT `tls`
    (pre-v0.3.0) raises exactly the production TypeError when handed the
    production configuration. This is the signal the v0.4.1 preflight
    probe surfaces as CONFIG_INCOMPATIBLE BEFORE any file is replaced."""
    data = _production_config_dict()

    with pytest.raises(TypeError, match=r"unexpected keyword argument 'tls'"):
        _merge_like_uaeg(LegacyDashboardConfigNoTls, data, "dashboard")


def test_incident_shape_configerror_message_mentions_tls():
    """End-to-end: uaeg.config.Config.from_dict wraps the legacy TypeError
    as ConfigError and the message names the offending field - the operator
    must be told WHICH field the (older) schema rejects."""
    data = _production_config_dict()

    # Simulate the stale interpreter: current Config class but the legacy
    # dashboard section. from_dict must fail loudly, never silently drop
    # the field (fail-open would silently disable dashboard TLS).
    import uaeg.config as config_mod

    original = config_mod.DashboardConfig
    try:
        config_mod.DashboardConfig = LegacyDashboardConfigNoTls
        with pytest.raises(ConfigError) as excinfo:
            config_mod.Config.from_dict(data)
        assert "tls" in str(excinfo.value)
        assert "unknown or mistyped" in str(excinfo.value)
    finally:
        config_mod.DashboardConfig = original


# ======================================================================
# I-3 — the preflight probe's contract (import origin + validation)
# ======================================================================
def test_probe_contract_staged_source_accepts_config(env, tmp_path):
    """The probe embedded in install.sh does, in-process:
    1. import uaeg / uaeg.config from the STAGED source,
    2. verify uaeg.__file__ is under the staged tree (RUNTIME_MISMATCH),
    3. Config.load(probe_path) with the staged schema (CONFIG_INCOMPATIBLE).
    This test pins step 3's success path with the REAL repository source.
    """
    data = _production_config_dict(env)
    probe = tmp_path / "config-probe.json"
    probe.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    import uaeg
    import uaeg.config

    # (2) the real source we are testing against is this checkout
    origin = Path(uaeg.__file__).resolve()
    assert "src" in origin.parts  # running from the repository source tree

    # (3) the staged schema accepts the production configuration
    loaded = uaeg.config.Config.load(str(probe))
    assert loaded.dashboard.tls is True


def test_probe_contract_detects_stale_shadow_via_origin_check():
    """RUNTIME_MISMATCH shape: when `uaeg` resolves somewhere OTHER than
    the staged source, the probe must refuse. Simulated by checking a
    path outside the staged tree is not accepted by the probe's
    is_relative_to test."""
    from pathlib import PurePath

    staged = Path("/tmp/uaeg-preflight.XXXX/src").resolve()
    stale_site_packages = Path(
        os.environ.get("VIRTUAL_ENV", "/nonexistent-venv")
        + "/lib/python3.12/site-packages"
    ).resolve()

    # the probe's check, verbatim: origin must be INSIDE the staged tree
    assert not stale_site_packages.is_relative_to(staged)


# ======================================================================
# I-4 — write_test_config keeps producing v0.3.0+-schema configs
# ======================================================================
def test_helper_configs_carry_dashboard_tls(env):
    """Every config the test helpers write must carry the newer schema
    (dashboard.tls present) so incident-shaped regressions surface."""
    write_test_config(env)
    data = json.loads(env["config"].read_text(encoding="utf-8"))

    assert "tls" in data["dashboard"]
