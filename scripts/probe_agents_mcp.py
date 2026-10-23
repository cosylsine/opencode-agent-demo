"""Check actual OpenCode custom agent permissions and MCP transport without a model."""

import asyncio
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    from packages.adapters.opencode import verify_permissions

    state = ROOT / "var/d0-business"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ)
    for kind in ("CONFIG", "DATA", "CACHE", "STATE"):
        env["XDG_" + kind + "_HOME"] = str(state / kind.lower())
    env["OPENCODE_SERVER_PASSWORD"] = secrets.token_urlsafe(32)
    env["MCP_SERVICE_TOKEN"] = secrets.token_urlsafe(32)
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
    env.pop("OPENCODE_CONFIG_CONTENT", None)
    env.pop("OPENCODE_DISABLE_PROJECT_CONFIG", None)
    config = json.loads((ROOT / "opencode.json").read_text())
    config["mcp"]["industrial_control"]["command"] = [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "apps.mcp_server",
    ]
    path = state / "config.json"
    path.write_text(json.dumps(config))
    env["OPENCODE_CONFIG"] = str(path)
    result = {}
    with (state / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                os.environ.get("OPENCODE_BIN", str(Path(__file__).resolve().parents[1] / "bin/opencode")),
                "serve",
                "--pure",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                auth=("opencode", env["OPENCODE_SERVER_PASSWORD"]),
                trust_env=False,
                timeout=10,
            ) as client:
                for _ in range(60):
                    try:
                        response = await client.get("/global/health")
                        response.raise_for_status()
                        break
                    except httpx.HTTPError:
                        await asyncio.sleep(0.25)
                agents = (await client.get("/agent")).json()
                business = [
                    {"name": a["name"], "permission": a.get("permission")}
                    for a in agents
                    if a["name"] in ("monitor", "control", "reception")
                ]
                if len(business) != 3:
                    raise RuntimeError("custom agents missing")
                verify_permissions(agents)
                mcp = (await client.get("/mcp")).json()
                result = {
                    "schema_version": "1.0",
                    "version": "1.18.31",
                    "effective_permissions_checked": True,
                    "agents": business,
                    "mcp_status": mcp.get("industrial_control", {}).get("status"),
                    "real_model_calls": "NOT_RUN: provider/model not configured",
                }
                if result["mcp_status"] != "connected":
                    raise RuntimeError("MCP not connected")
            # Test official MCP stdio negotiation and exact tool discovery separately.
            params = StdioServerParameters(
                command=str(ROOT / ".venv/bin/python"),
                args=["-m", "apps.mcp_server"],
                cwd=str(ROOT),
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "MCP_SERVICE_TOKEN": env["MCP_SERVICE_TOKEN"],
                    "INTERNAL_API_BASE_URL": "http://127.0.0.1:18000/internal/v1",
                },
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    result["stdio_tools"] = [t.name for t in listed.tools]
                    denied = await session.call_tool(
                        "get_recent_window", {"call_context_token": "invalid-probe"}
                    )
                    result["invalid_credential_denied"] = "TOOL_READ_FAILED" in str(denied)
            (ROOT / "docs/runbooks/agent-mcp-probe.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result, indent=2))
        finally:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


if __name__ == "__main__":
    asyncio.run(main())
