"""Four bounded, read-only tools. The service credential never enters model context."""

import os

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("industrial_control")


async def read(tool, token):
    base = os.environ.get("INTERNAL_API_BASE_URL", "http://127.0.0.1:18000/internal/v1")
    if not base.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise ValueError("Internal API must be loopback")
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        response = await client.post(
            base + "/tools/" + tool,
            headers={"X-MCP-Service-Token": os.environ["MCP_SERVICE_TOKEN"]},
            json={"schema_version": "1.0", "call_context_token": token},
        )
        if response.status_code != 200:
            # No token, headers, or raw request in tool errors.
            return {
                "schema_version": "1.0",
                "error": {"code": "TOOL_READ_FAILED", "status": response.status_code},
            }
        return response.json()


@mcp.tool()
async def get_recent_window(call_context_token: str) -> dict:
    """Read the frozen window reference and actual PID for this Monitor request."""
    return await read("get_recent_window", call_context_token)


@mcp.tool()
async def calculate_metrics(call_context_token: str) -> dict:
    """Read deterministic metrics of the authorized frozen snapshot."""
    return await read("calculate_metrics", call_context_token)


@mcp.tool()
async def detect_state(call_context_token: str) -> dict:
    """Read deterministic rule assessment of the authorized frozen snapshot."""
    return await read("detect_state", call_context_token)


@mcp.tool()
async def get_tuning_context(call_context_token: str) -> dict:
    """Read actual PID, current best, constraints and history for this Control request."""
    return await read("get_tuning_context", call_context_token)


if __name__ == "__main__":
    mcp.run(transport="stdio")
