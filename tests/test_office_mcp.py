"""Exercise FastMCP's real argument decoding, including nested Pydantic rules."""

import pytest


@pytest.mark.asyncio
async def test_nested_mcp_parameters_cross_http_as_json(monkeypatch):
    from apps.office_mcp import mcp
    captured = []

    class Response:
        status_code = 200
        def json(self):
            return {"valid": True, "files": []}

    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, headers, json):
            captured.append(json)
            # The production httpx encoder requires ordinary JSON values.
            __import__("json").dumps(json)
            return Response()

    monkeypatch.setenv("INTERNAL_API_BASE_URL", "http://127.0.0.1:3000/internal/v1")
    monkeypatch.setenv("MCP_SERVICE_TOKEN", "test-service-key")
    monkeypatch.setattr("apps.office_mcp.httpx.AsyncClient", Client)
    result = await mcp.call_tool("validate_table", {"context_token": "scoped-test-token", "file_id": "test-file",
        "rules": [{"column": "工时", "type": "number", "minimum": 0.0}]})
    assert captured[0]["arguments"]["rules"][0]["column"] == "工时"
    assert captured[0]["arguments"]["rules"][0]["minimum"] == 0
    assert captured[0]["context_token"] == "scoped-test-token"
    assert "valid" in str(result)


@pytest.mark.asyncio
async def test_document_content_is_forwarded_and_unknown_fields_are_rejected(monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError

    from apps.office_mcp import mcp

    captured = []

    class Response:
        status_code = 200

        def json(self):
            return {"files": []}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers, json):
            captured.append(json)
            return Response()

    monkeypatch.setenv("INTERNAL_API_BASE_URL", "http://127.0.0.1:3000/internal/v1")
    monkeypatch.setenv("MCP_SERVICE_TOKEN", "test-service-key")
    monkeypatch.setattr("apps.office_mcp.httpx.AsyncClient", Client)
    arguments = {"context_token": "scoped-test-token", "title": "总结", "content": "## 正文\n\n这是不能被丢弃的总结内容。"}
    await mcp.call_tool("generate_document", arguments)
    assert captured[0]["arguments"]["content"] == arguments["content"]
    with pytest.raises(ToolError, match="未知工具参数：body"):
        await mcp.call_tool("generate_document", {**arguments, "body": "不能被静默丢弃"})
    with pytest.raises(ToolError, match="Word 正文不能为空"):
        await mcp.call_tool("generate_document", {"context_token": "scoped-test-token", "title": "仅标题"})
    assert len(captured) == 1
    schema = next(t.inputSchema for t in await mcp.list_tools() if t.name == "generate_document")
    assert schema["additionalProperties"] is False
    assert "正文" in schema["properties"]["content"]["description"]


@pytest.mark.asyncio
async def test_batch_tools_keep_lists_constraints_and_source_validation(monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError

    from apps.office_mcp import mcp

    captured = []

    class Response:
        status_code = 200

        def json(self):
            return {"files": []}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers, json):
            captured.append(json)
            return Response()

    monkeypatch.setenv("INTERNAL_API_BASE_URL", "http://127.0.0.1:3000/internal/v1")
    monkeypatch.setenv("MCP_SERVICE_TOKEN", "test-service-key")
    monkeypatch.setattr("apps.office_mcp.httpx.AsyncClient", Client)
    await mcp.call_tool("process_pdf", {"context_token": "scoped", "file_ids": ["first", "second"], "operation": "merge"})
    assert captured[0]["arguments"]["file_ids"] == ["first", "second"]
    await mcp.call_tool("redact_text", {"context_token": "scoped", "content": "张三", "categories": [], "custom_terms": ["张三"]})
    assert captured[1]["arguments"]["categories"] == []
    with pytest.raises(ToolError):
        await mcp.call_tool("bundle_files", {"context_token": "scoped", "file_ids": []})
    with pytest.raises(ToolError):
        await mcp.call_tool("process_text", {"context_token": "scoped", "content": "填写文本", "file_id": "double-source"})
    tools = await mcp.list_tools()
    assert len(tools) == 17
    schema = next(t.inputSchema for t in tools if t.name == "bundle_files")
    assert schema["properties"]["file_ids"]["maxItems"] == 20
    assert len(captured) == 2
