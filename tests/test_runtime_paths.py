import importlib.util
from pathlib import Path


def test_launcher_uses_explicit_binary_and_bypasses_loopback_proxy(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/launch_opencode.py"
    monkeypatch.setenv("OPENCODE_BIN", "/test/project/opencode")
    spec = importlib.util.spec_from_file_location("migration_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.BINARY == "/test/project/opencode"
    env = {"NO_PROXY": "existing.internal", "no_proxy": "localhost"}
    module.ensure_loopback_proxy_bypass(env)
    assert env["NO_PROXY"] == env["no_proxy"]
    assert set(env["NO_PROXY"].split(",")) == {
        "existing.internal", "localhost", "127.0.0.1"
    }
