import json

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts.model_gateway import create_app

KEY = "migration-test-key-" + "x" * 40


def gateway(tmp_path, handler):
    key_file = tmp_path / "key"
    key_file.write_text(KEY)
    return TestClient(create_app(key_file, transport=httpx.MockTransport(handler)))


def test_rejects_missing_wrong_credentials_and_private_routes(tmp_path):
    def unexpected(request):
        pytest.fail("Rejected request reached upstream")

    with gateway(tmp_path, unexpected) as client:
        assert client.get("/v1/models").status_code == 401
        assert client.get("/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
        for path in ("/internal/v1", "/docs", "/v1/responses", "/health"):
            assert client.get(path, headers={"Authorization": "Bearer " + KEY}).status_code == 404


def test_tool_schema_and_stream_preserved_without_forwarding_secret(tmp_path):
    payload = {
        "model": "qwen3.6-35b-a3b", "stream": True,
        "messages": [{"role": "user", "content": "ready"}],
        "tools": [{"type": "function", "function": {"name": "echo"}}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "probe"}},
    }
    stream = b'data: {"choices":[]}\n\ndata: [DONE]\n\n'

    def respond(request):
        assert str(request.url) == "http://127.0.0.1:8000/v1/chat/completions"
        assert json.loads(request.content) == payload
        assert "authorization" not in request.headers
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(stream)
        )

    with gateway(tmp_path, respond) as client:
        response = client.post(
            "/v1/chat/completions", json=payload, headers={"Authorization": "Bearer " + KEY}
        )
        assert response.status_code == 200
        assert response.content == stream
        assert response.headers["content-type"].startswith("text/event-stream")


def test_unavailable_upstream_is_502(tmp_path):
    def unavailable(request):
        raise httpx.ConnectError("unavailable", request=request)

    with gateway(tmp_path, unavailable) as client:
        assert client.get("/v1/models", headers={"Authorization": "Bearer " + KEY}).status_code == 502


def test_model_list_and_upstream_errors_preserved(tmp_path):
    def respond(request):
        return httpx.Response(429, stream=httpx.ByteStream(b'{"error":"busy"}'))

    with gateway(tmp_path, respond) as client:
        response = client.get("/v1/models", headers={"Authorization": "Bearer " + KEY})
        assert response.status_code == 429
        assert response.json() == {"error": "busy"}
