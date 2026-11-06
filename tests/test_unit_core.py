"""Unit tests: security, config, store."""

from __future__ import annotations

import json

import pytest

from uaeg import security
from uaeg.config import Config, ConfigError
from uaeg.db import Store


# ----------------------------------------------------------------------
# security
# ----------------------------------------------------------------------
def test_secret_format():
    secret = security.generate_secret()
    assert secret.startswith("uaeg_")
    body = secret[len("uaeg_"):]
    assert len(body) == 43  # 256 bits base64url
    assert all(c.isalnum() or c in "-_" for c in body)


def test_verifier_is_stable_and_non_invertible():
    secret = security.generate_secret()
    v1 = security.verifier(secret)
    v2 = security.verifier(secret)
    assert v1 == v2
    assert len(v1) == 64  # sha256 hex
    assert secret not in v1


def test_new_key_unique():
    a = security.new_key("a")
    b = security.new_key("b")
    assert a.secret != b.secret
    assert a.key_id != b.key_id
    assert a.prefix.startswith("uaeg_")


# ----------------------------------------------------------------------
# config
# ----------------------------------------------------------------------
def test_config_roundtrip(tmp_path):
    cfg = Config()
    cfg.public_host = "gw.example.com"
    cfg.mcp.port = 4001
    cfg.dashboard.port = 4002
    cfg.exec.restricted_paths = ["/etc/uaeg"]
    path = tmp_path / "config.json"
    cfg.save(path)
    loaded = Config.load(path)
    assert loaded.public_host == "gw.example.com"
    assert loaded.mcp.port == 4001
    assert loaded.dashboard.port == 4002
    assert loaded.exec.restricted_paths == ["/etc/uaeg"]
    assert loaded.mcp_url() == "https://gw.example.com:4001/mcp"


def test_config_validation_rules(tmp_path):
    cfg = Config()
    cfg.mcp.port = cfg.dashboard.port = 3902
    with pytest.raises(ConfigError):
        cfg.validate()

    cfg = Config()
    cfg.dashboard.bind = "0.0.0.0"
    with pytest.raises(ConfigError):
        cfg.validate()

    cfg = Config()
    cfg.dashboard.bind = "0.0.0.0"
    cfg.dashboard.allow_public = True
    warnings = cfg.validate()
    assert any("ALL interfaces" in w for w in warnings)

    cfg = Config()
    cfg.tls.mode = "manual"
    with pytest.raises(ConfigError):
        cfg.validate()


def test_config_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        Config.load(tmp_path / "nope.json")


# ----------------------------------------------------------------------
# store
# ----------------------------------------------------------------------
def test_store_keys_lifecycle(tmp_path):
    store = Store(tmp_path / "uaeg.db")
    material = security.new_key("Claude")
    store.create_key(
        key_id=material.key_id, name=material.name,
        prefix=material.prefix, verifier_hex=material.verifier,
    )

    found = store.find_active_key_by_verifier(security.verifier(material.secret))
    assert found and found["key_id"] == material.key_id
    assert "verifier" in found  # internal row; secret itself is absent
    assert store.find_active_key_by_verifier(security.verifier("uaeg_wrong")) is None

    store.touch_key_last_used(material.key_id)
    keys = store.list_keys()
    assert keys[0]["last_used_at"] is not None

    revoked = store.revoke_key(material.key_id)
    assert revoked["status"] == "revoked"
    assert store.find_active_key_by_verifier(security.verifier(material.secret)) is None
    store.close()


def test_store_jobs_and_sessions(tmp_path):
    store = Store(tmp_path / "uaeg.db")
    store.insert_job("job-1", "sleep 1", "/", 123, "k-1", "tester")
    store.update_job("job-1", "completed", exit_code=0, finished=True)
    job = store.get_job("job-1")
    assert job["status"] == "completed"
    assert job["exit_code"] == 0
    assert job["finished_at"]

    store.insert_session("sess-1", "bash", "/tmp", 456, "k-1", "tester")
    assert len(store.open_sessions()) == 1
    store.mark_open_sessions("interrupted")
    assert store.open_sessions() == []
    assert store.get_session("sess-1")["status"] == "interrupted"

    store.add_log("k-1", "tester", "shell_exec", "ok", {"command": "id"}, exit_code=0)
    logs = store.list_logs()
    assert logs[0]["tool"] == "shell_exec"
    assert logs[0]["meta"]["command"] == "id"
    assert json.dumps(logs[0])  # serializable
    store.close()
