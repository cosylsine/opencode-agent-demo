"""Check supervisor configuration and real wildcard-listener port conflicts."""

import importlib.util
import json
import socket
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "lan_runtime", Path(__file__).resolve().parents[1] / "build/runtime.py"
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


@pytest.fixture
def lan_env(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_PORT", "3000")
    monkeypatch.setenv("OPENCODE_PORT", "14097")
    monkeypatch.setenv("VLLM_BASE_URL", "http://127.0.0.1:4625/v1")
    monkeypatch.setenv("APP_HOST", "0.0.0.0")
    monkeypatch.setenv("DEMO_ORIGIN", "http://10.70.100.220:3000")
    monkeypatch.setenv("DEMO_ALLOWED_CIDRS", "10.70.0.0/17")
    monkeypatch.setattr(runtime, "STATE", tmp_path)
    return monkeypatch


def test_configure_preserves_public_origin_and_private_service_urls(lan_env):
    (runtime.STATE / "runtime-secrets.json").write_text(json.dumps({
        "OPENCODE_SERVER_PASSWORD": "p" * 40, "MCP_SERVICE_TOKEN": "m" * 40,
    }))
    # configure updates environment in production; isolate those writes here.
    with lan_env.context() as context:
        context.setattr(runtime.os, "environ", dict(runtime.os.environ))
        assert runtime.configure() == (3000, 14097)
        assert runtime.os.environ["DEMO_ORIGIN"] == "http://10.70.100.220:3000"
        assert runtime.os.environ["DEMO_OPENCODE_URL"] == "http://127.0.0.1:14097"
        assert runtime.os.environ["INTERNAL_API_BASE_URL"] == "http://127.0.0.1:3000/internal/v1"


@pytest.mark.parametrize("key,value", [
    ("APP_HOST", "10.70.100.220"),
    ("DEMO_ORIGIN", "http://10.70.100.220:3001"),
    ("DEMO_ORIGIN", "http://10.70.100.220:3000/"),
    ("DEMO_ORIGIN", "http://127.0.0.1:3000"),
    ("DEMO_ORIGIN", "http://user@10.70.100.220:3000"),
    ("DEMO_ALLOWED_CIDRS", ""),
    ("DEMO_ALLOWED_CIDRS", "10.70.100.220/24"),
])
def test_invalid_lan_configuration_fails_early(lan_env, key, value):
    lan_env.setenv(key, value)
    with pytest.raises(ValueError):
        runtime.web_settings(3000)


def test_wildcard_preflight_catches_listener_outside_127_0_0_1(lan_env):
    with socket.socket() as listener:
        listener.bind(("127.0.0.2", 0))
        listener.listen()
        port = listener.getsockname()[1]
        lan_env.setenv("APP_PORT", str(port))
        lan_env.setenv("DEMO_ORIGIN", f"http://10.70.100.220:{port}")
        with pytest.raises(ValueError, match="被占用"):
            runtime.preflight()


def test_default_web_settings_stay_loopback_only(monkeypatch):
    for key in ("APP_HOST", "DEMO_ORIGIN", "DEMO_ALLOWED_CIDRS"):
        monkeypatch.delenv(key, raising=False)
    assert runtime.web_settings(3000) == ("127.0.0.1", "http://127.0.0.1:3000")


def test_existing_catalog_gets_tuning_fix_without_losing_custom_skills(tmp_path):
    source = Path(__file__).resolve().parents[1] / "source/.opencode"
    target = tmp_path / "catalog"
    runtime.seed_directory(target, source)
    old = target / "skills/control-safety/SKILL.md"
    old.write_text("Version 2.0. Only EXAMPLE data is supported.")
    custom = target / "skills/operator-custom/SKILL.md"
    custom.parent.mkdir()
    custom.write_text("操作员自己的技能")
    # Re-seeding alone intentionally preserves a persistent catalog.
    runtime.seed_directory(target, source)
    assert old.read_text().startswith("Version 2.0.")
    runtime.sync_tuning_catalog(target, source)
    assert old.read_bytes() == (source / "skills/control-safety/SKILL.md").read_bytes()
    assert custom.read_text() == "操作员自己的技能"
    backups = list((target / ".builtin-backups/tuning-v3").rglob("*"))
    saved = [p for p in backups if p.is_file()]
    assert len(saved) == 1
    assert saved[0].read_text() == "Version 2.0. Only EXAMPLE data is supported."
    runtime.sync_tuning_catalog(target, source)
    assert [p for p in (target / ".builtin-backups/tuning-v3").rglob("*") if p.is_file()] == saved
