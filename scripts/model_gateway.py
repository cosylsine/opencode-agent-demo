"""Authenticated tailnet access to the existing GPU 0/1 model, without restarting it."""

import argparse
import hmac
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

UPSTREAM = "http://127.0.0.1:8000"
ALLOWED = {("GET", "/v1/models"), ("POST", "/v1/chat/completions")}
BODY_LIMIT = 10 * 1024 * 1024


def create_app(key_file: Path, *, transport=None) -> FastAPI:
    key = key_file.read_text().strip()
    if len(key) < 32:
        raise ValueError("Model gateway key must contain at least 32 characters")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def forward(request: Request, path: str):
        route = "/" + path
        if (request.method, route) not in ALLOWED:
            return JSONResponse({"error": "Not found"}, status_code=404)
        authorization = request.headers.get("authorization", "")
        expected = ("Bearer " + key).encode()
        if not hmac.compare_digest(authorization.encode(), expected):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"error": "Invalid request length"}, status_code=400)
        if length < 0:
            return JSONResponse({"error": "Invalid request length"}, status_code=400)
        if length > BODY_LIMIT:
            return JSONResponse({"error": "Request too large"}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > BODY_LIMIT:
                return JSONResponse({"error": "Request too large"}, status_code=413)
        client = httpx.AsyncClient(
            base_url=UPSTREAM, trust_env=False, transport=transport,
            timeout=httpx.Timeout(30.0, read=None),
        )
        try:
            upstream = client.build_request(
                request.method, route, params=request.query_params, content=bytes(body),
                headers={
                    "Content-Type": request.headers.get("content-type", "application/json"),
                    "Accept-Encoding": "identity",
                },
            )
            response = await client.send(upstream, stream=True)
        except httpx.HTTPError:
            await client.aclose()
            return JSONResponse({"error": "Model service unavailable"}, status_code=502)

        async def chunks():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return StreamingResponse(
            chunks(), status_code=response.status_code,
            headers={
                "Content-Type": response.headers.get("content-type", "application/json"),
                "Cache-Control": "no-store",
            },
        )

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    args = parser.parse_args()
    uvicorn.run(create_app(args.key_file), host="100.64.0.6", port=18084, access_log=False)


if __name__ == "__main__":
    main()
