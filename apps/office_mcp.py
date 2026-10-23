"""Office MCP tools forward only scoped calls to the local backend."""

import inspect
import os

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic_core import PydanticUndefined

from packages.adapters.office_tools import TOOL_REGISTRY


class OfficeMCP(FastMCP):
    async def call_tool(self, name, arguments):
        # FastMCP's generated argument model otherwise silently ignores extras.
        if name in TOOL_REGISTRY:
            unknown = set(arguments) - {"context_token", *TOOL_REGISTRY[name][2].model_fields}
            if unknown:
                raise ToolError("未知工具参数：" + "、".join(sorted(unknown)) + "；请按工具定义传参，不会静默丢弃正文。")
        return await super().call_tool(name, arguments)

    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.inputSchema["additionalProperties"] = False
            properties = TOOL_REGISTRY[tool.name][2].model_json_schema()["properties"]
            for name, definition in properties.items():
                if definition.get("description"):
                    tool.inputSchema["properties"][name]["description"] = definition["description"]
        return tools


mcp = OfficeMCP("office")


def register(name, description, model):
    async def invoke(context_token: str, **arguments) -> dict:
        # FastMCP materializes nested parameters as Pydantic instances.
        # Convert them back to JSON values before crossing the HTTP boundary.
        arguments = model.model_validate(arguments).model_dump(mode="json")
        base = os.environ["INTERNAL_API_BASE_URL"].removesuffix("/internal/v1")
        if not base.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("Office backend must listen on loopback")
        async with httpx.AsyncClient(timeout=65, trust_env=False) as client:
            response = await client.post(
                base + "/internal/v1/office/" + name,
                headers={"X-MCP-Service-Token": os.environ["MCP_SERVICE_TOKEN"]},
                json=dict(context_token=context_token, arguments=arguments),
            )
            if response.status_code != 200:
                error = response.json().get("error", {})
                return {
                    "error": {
                        "code": error.get("code", "TOOL_FAILED"),
                        "message": error.get("message", "工具执行失败"),
                    }
                }
            return response.json()

    parameters = [
        inspect.Parameter("context_token", inspect.Parameter.KEYWORD_ONLY, annotation=str)
    ]
    for field, definition in model.model_fields.items():
        default = (
            definition.default
            if definition.default is not PydanticUndefined
            else inspect.Parameter.empty
        )
        parameters.append(
            inspect.Parameter(
                field,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=definition.rebuild_annotation(),
                default=default,
            )
        )
    invoke.__signature__ = inspect.Signature(parameters, return_annotation=dict)
    invoke.__name__ = name
    mcp.tool(name=name, description=description)(invoke)


for tool_id, (title, description, model) in TOOL_REGISTRY.items():
    register(tool_id, title + "。" + description + " context_token 必须使用本轮执行凭证。", model)


if __name__ == "__main__":
    mcp.run(transport="stdio")
