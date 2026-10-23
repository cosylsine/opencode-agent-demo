"""Exercise the local vLLM endpoint before selecting it for the PID agents."""

import argparse
import json
import os

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="qwen3.6-35b-a3b")
    args = parser.parse_args()

    result = {"model": args.model}
    common = {
        "model": args.model,
        "max_tokens": 128,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    tool = {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo a short string",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }

    headers = {}
    if key := os.environ.get("DEMO_MODEL_API_KEY"):
        headers["Authorization"] = "Bearer " + key
    with httpx.Client(
        base_url=args.base_url.rstrip("/") + "/", headers=headers, trust_env=False, timeout=90
    ) as client:
        models = client.get("models")
        result["models_http"] = models.status_code
        result["model_listed"] = models.is_success and args.model in {
            item.get("id") for item in models.json().get("data", [])
        }
        if not result["model_listed"]:
            print(json.dumps(result, indent=2))
            raise SystemExit(1)

        plain = client.post(
            "chat/completions",
            json={**common, "messages": [{"role": "user", "content": "Reply only OK."}]},
        )
        result["plain_http"] = plain.status_code
        result["plain_text"] = bool(
            plain.is_success and plain.json()["choices"][0]["message"].get("content")
        )

        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"status": {"type": "string", "enum": ["ready"]}},
            "required": ["status"],
        }
        structured = client.post(
            "chat/completions",
            json={
                **common,
                "messages": [{"role": "user", "content": "Return status ready as JSON."}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "probe", "schema": schema, "strict": True},
                },
            },
        )
        result["json_schema_http"] = structured.status_code
        try:
            result["json_schema"] = (
                structured.is_success
                and json.loads(structured.json()["choices"][0]["message"]["content"])
                == {"status": "ready"}
            )
        except (KeyError, TypeError, ValueError):
            result["json_schema"] = False

        tool_body = {
            **common,
            "messages": [{"role": "user", "content": "Call echo with text ready."}],
            "tools": [tool],
        }
        automatic = client.post("chat/completions", json={**tool_body, "tool_choice": "auto"})
        result["auto_tool_http"] = automatic.status_code
        result["auto_tool_call"] = bool(
            automatic.is_success
            and any(
                call.get("function", {}).get("name") == "echo"
                for call in automatic.json()["choices"][0]["message"].get("tool_calls") or []
            )
        )

        named = client.post(
            "chat/completions",
            json={**tool_body, "tool_choice": {"type": "function", "function": {"name": "echo"}}},
        )
        result["named_tool_http"] = named.status_code
        result["named_tool_call"] = bool(
            named.is_success
            and any(
                call.get("function", {}).get("name") == "echo"
                for call in named.json()["choices"][0]["message"].get("tool_calls") or []
            )
        )

    result["ready_for_agents"] = all(
        (
            result["model_listed"],
            result["plain_text"],
            result["json_schema"],
            result["auto_tool_call"],
            result["named_tool_call"],
        )
    )
    print(json.dumps(result, indent=2))
    if not result["ready_for_agents"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
