"""The launched MCP process must inherit the application port after config merges."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "source"


@pytest.mark.parametrize("configured_base", [None, "http://127.0.0.1:3000/internal/v1"])
def test_launcher_and_project_config_agree_on_mcp_base(tmp_path, monkeypatch, configured_base):
    monkeypatch.setenv("OPENCODE_BIN", "/test/opencode")
    spec = importlib.util.spec_from_file_location("mcp_launcher", SOURCE / "scripts/launch_opencode.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    env = dict(os.environ)
    env.update({"OPENCODE_SERVER_PASSWORD": "test-password", "MCP_SERVICE_TOKEN": "test-token",
                "DEMO_PROVIDER": "test-provider", "DEMO_MODEL": "test-model"})
    env.pop("INTERNAL_API_BASE_URL", None)
    if configured_base:
        env["INTERNAL_API_BASE_URL"] = configured_base
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps({"provider": {"test-provider": {}}}))
    env["DEMO_PROVIDER_CONFIG"] = str(provider)
    (tmp_path / "opencode.json").write_bytes((SOURCE / "opencode.json").read_bytes())
    monkeypatch.setattr(launcher.os, "environ", env)
    monkeypatch.setattr(launcher.subprocess, "check_output", lambda *args, **kwargs: "1.18.31\n")
    monkeypatch.setattr(launcher.os, "chdir", lambda *_: None)
    calls = []
    monkeypatch.setattr(launcher.os, "execv", lambda *args: calls.append(args))
    launcher.main()
    assert calls
    expected = configured_base or "http://127.0.0.1:18000/internal/v1"
    runtime = json.loads((tmp_path / "var/opencode/runtime-config.json").read_text())
    project = json.loads((tmp_path / "opencode.json").read_text())
    reference = project["mcp"]["industrial_control"]["environment"]["INTERNAL_API_BASE_URL"]
    assert reference == "{env:INTERNAL_API_BASE_URL}"
    assert env["INTERNAL_API_BASE_URL"] == expected
    assert runtime["mcp"]["industrial_control"]["environment"]["INTERNAL_API_BASE_URL"] == expected
