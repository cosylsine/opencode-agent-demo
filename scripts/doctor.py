"""Read-only dependency report; never prints secret values."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.orchestrator.config import Settings
from packages.adapters.live_telemetry import LiveTelemetry
from packages.adapters.opencode import OpenCodeClient
from packages.contracts.common import now
from packages.domain.core import SCENARIOS


async def main():
    config = Settings.from_env()
    checks = []
    for name, command in [
        ("python", [sys.executable, "--version"]),
        ("node", ["node", "--version"]),
        ("opencode", [os.environ.get("OPENCODE_BIN", str(Path(__file__).resolve().parents[1] / "bin/opencode")), "--version"]),
        ("systemd", ["systemctl", "--user", "is-system-running"]),
    ]:
        try:
            r = subprocess.run(command, capture_output=True, text=True, timeout=10)
            checks.append(
                {"name": name, "ok": r.returncode == 0, "version": r.stdout.strip()[:100]}
            )
        except (OSError, subprocess.TimeoutExpired):
            checks.append({"name": name, "ok": False})
    for port in (14096, 18000):
        with socket.socket() as sock:
            checks.append(
                {"name": f"port:{port}", "listening": sock.connect_ex(("127.0.0.1", port)) == 0}
            )
    checks.append(
        {
            "name": "configuration",
            "provider_model_configured": bool(config.provider and config.model),
            "opencode_password_configured": bool(config.opencode_password),
            "mcp_service_token_configured": bool(config.service_token),
            "local_dev": config.local_dev,
        }
    )
    checks.append(
        {
            "name": "disk",
            "free_bytes": shutil.disk_usage(Path.cwd()).free,
            "state_dir_exists": config.state_dir.exists(),
        }
    )
    loop = LiveTelemetry(config.state_dir)
    checks.append(
        {
            "name": "virtual_loop",
            "running": loop.is_running(),
            "window_ready": {scenario: loop.window_ready(scenario, now()) for scenario in SCENARIOS},
        }
    )
    client = OpenCodeClient(config)
    try:
        # A real startup probe is opt-in because it invokes the configured model.
        if "--model-probe" in sys.argv:
            await client.probe()
            checks.append(
                {"name": "real_model_probe", "ready": client.ready, "error_code": client.error_code}
            )
    finally:
        await client.close()
    print(json.dumps({"schema_version": "1.0", "checks": checks}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
