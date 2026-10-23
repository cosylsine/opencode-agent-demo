"""Exec the pinned runtime in project-isolated XDG directories."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARY = os.environ.get("OPENCODE_BIN", str(Path(__file__).resolve().parents[1] / "bin/opencode"))


def ensure_loopback_proxy_bypass(env):
    """Keep project-local HTTP calls out of inherited shell proxies."""
    entries = []
    for key in ("NO_PROXY", "no_proxy"):
        entries.extend(item.strip() for item in env.get(key, "").split(",") if item.strip())
    for host in ("127.0.0.1", "localhost"):
        if host not in entries:
            entries.append(host)
    value = ",".join(dict.fromkeys(entries))
    env["NO_PROXY"] = value
    env["no_proxy"] = value


def main():
    if subprocess.check_output([BINARY, "--version"], text=True).strip() != "1.18.31":
        raise SystemExit("OpenCode version must be 1.18.31")
    required = ["OPENCODE_SERVER_PASSWORD", "MCP_SERVICE_TOKEN", "DEMO_PROVIDER", "DEMO_MODEL"]
    if any(not os.environ.get(k) for k in required):
        raise SystemExit("Missing required runtime environment; see deploy/runtime.env.example")
    runtime = ROOT / "var/opencode"
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime, 0o700)
    for kind in ("CONFIG", "DATA", "CACHE", "STATE"):
        os.environ["XDG_" + kind + "_HOME"] = str(runtime / kind.lower())
    ensure_loopback_proxy_bypass(os.environ)
    os.environ["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
    os.environ.pop("OPENCODE_CONFIG_CONTENT", None)
    # External provider-only configuration must be explicitly selected by the operator.
    provider_path = os.environ.get("DEMO_PROVIDER_CONFIG")
    if not provider_path:
        raise SystemExit("Set DEMO_PROVIDER_CONFIG to a dedicated provider-only JSON file")
    provider = json.loads(Path(provider_path).read_text())
    if set(provider) != {"provider"} or os.environ["DEMO_PROVIDER"] not in provider["provider"]:
        raise SystemExit("Provider file must contain only the selected provider configuration")
    config = json.loads((ROOT / "opencode.json").read_text())
    config.update(provider)
    config["model"] = os.environ["DEMO_PROVIDER"] + "/" + os.environ["DEMO_MODEL"]
    config["mcp"]["industrial_control"]["command"] = [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "apps.mcp_server",
    ]
    # OpenCode also merges the project's opencode.json. Resolve the same
    # environment variable there so it cannot restore the old 18000 port.
    internal_base = os.environ.setdefault(
        "INTERNAL_API_BASE_URL", "http://127.0.0.1:18000/internal/v1"
    )
    config["mcp"]["industrial_control"]["environment"]["INTERNAL_API_BASE_URL"] = internal_base
    config["mcp"]["office"]["command"] = [str(ROOT / ".venv/bin/python"), "-m", "apps.office_mcp"]
    config["mcp"]["office"]["environment"]["INTERNAL_API_BASE_URL"] = internal_base
    config["enabled_providers"] = [os.environ["DEMO_PROVIDER"]]
    output = runtime / "runtime-config.json"
    output.write_text(json.dumps(config))
    os.chmod(output, 0o600)
    os.environ["OPENCODE_CONFIG"] = str(output)
    os.chdir(ROOT)
    os.execv(BINARY, [BINARY, "serve", "--pure", "--hostname", "127.0.0.1", "--port",
                     os.environ.get("DEMO_OPENCODE_PORT", "14096")])


if __name__ == "__main__":
    main()
