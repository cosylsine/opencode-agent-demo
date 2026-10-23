"""One constrained response per ST stage, using the configured compatible model.

See https://docs.vllm.ai/en/stable/features/structured_outputs/ . No tools,
conversation history, or automatic HTTP retries are sent to this endpoint.
"""

import json
import os
import re
from pathlib import Path

import httpx
from pydantic import ValidationError

from packages.contracts.st_code import STRepairStage
from packages.domain.core import DomainError


def model_connection(settings):
    try:
        config = json.loads(Path(os.environ["DEMO_PROVIDER_CONFIG"]).read_text())
        options = config["provider"][settings.provider]["options"]
        url = options["baseURL"].rstrip("/") + "/chat/completions"
        key = options.get("apiKey", "")
        variable = re.fullmatch(r"\{env:([A-Za-z_][A-Za-z_0-9]*)\}", key)
        if variable:
            key = os.environ[variable[1]]
        if not url.startswith(("http://", "https://")) or not key:
            raise ValueError()
        return url, key
    except (OSError, ValueError, KeyError, TypeError):
        raise DomainError(
            "ST_MODEL_NOT_CONFIGURED", "ST 模型服务配置不完整，请检查平台模型配置。", 503
        ) from None


async def generate(settings, schema, payload, instruction, request_id):
    url, key = model_connection(settings)
    output_schema = schema.model_json_schema()
    if schema is STRepairStage:
        # Keep historical checkpoints/contracts compatible while constraining new
        # model prose. The replacement code retains its existing size allowance.
        output_schema["$defs"]["STSourceEdit"]["properties"]["explanation"]["maxLength"] = 240
        if plan := payload.get("single_edit_repair"):
            output_schema["properties"]["edits"].update(minItems=1, maxItems=1)
            edit = output_schema["$defs"]["STSourceEdit"]["properties"]
            edit["line_start"]["const"] = plan["line_start"]
            edit["line_end"]["const"] = plan["line_end"]
            edit["bug_ids"]["items"]["enum"] = plan["allowed_bug_ids"]
    body = dict(
        model=settings.model,
        temperature=0.0,
        max_tokens=4096,
        chat_template_kwargs={"enable_thinking": False},
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": output_schema,
                "strict": True,
            },
        },
        messages=[
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ],
    )
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=900) as client:
            async with client.stream(
                "POST",
                url,
                json=body,
                headers={"Authorization": "Bearer " + key, "X-Request-ID": request_id},
            ) as response:
                if response.status_code != 200:
                    raise DomainError(
                        "ST_MODEL_PROTOCOL_ERROR",
                        f"ST 模型服务返回 HTTP {response.status_code}，未自动重发。",
                        503,
                    )
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 2_000_000:
                        raise DomainError(
                            "ST_MODEL_PROTOCOL_ERROR", "ST 模型响应过大，已停止读取。", 503
                        )
                    chunks.append(chunk)
        value = json.loads(b"".join(chunks))
        choice = value["choices"][0]
        if choice["finish_reason"] != "stop":
            raise DomainError(
                "ST_OUTPUT_INVALID",
                "当前阶段输出达到长度限制或未完整结束，请精简当前阶段结果。",
                503,
            )
        if choice["message"].get("tool_calls"):
            raise DomainError("ST_OUTPUT_INVALID", "本阶段不能调用工具。", 503)
        return schema.model_validate_json(choice["message"]["content"])
    except httpx.HTTPError:
        raise DomainError(
            "ST_RESULT_UNKNOWN", "ST 模型请求连接中断或超时，结果未知；未自动重发。", 503
        ) from None
    except ValidationError as exc:
        detail = "; ".join(
            ".".join(map(str, item["loc"])) + ": " + item["msg"]
            for item in exc.errors(include_input=False)[:4]
        )
        raise DomainError("ST_OUTPUT_INVALID", detail[:1500], 503) from None
    except (ValueError, KeyError, TypeError, IndexError):
        raise DomainError(
            "ST_OUTPUT_INVALID", "模型响应未返回完整的当前阶段 JSON 对象。", 503
        ) from None
