"""TEST-ONLY deterministic model for the real OpenCode/MCP deployment rehearsal.

Mounted into the test container; never copied into the application image.
It exercises tool transport and workflows, not inference or model quality.
"""

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "Qwen3.8-27B"
COUNTS = Counter()
LOCK = threading.Lock()
TRACE = deque(maxlen=8)
TOOL_RESULTS = {}


def text_parts(content):
    if isinstance(content, str):
        return [content]
    return [part.get("text", "") for part in content or [] if isinstance(part, dict)]


def agent_payload(messages):
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        for text in text_parts(message.get("content")):
            # OpenCode can prefix user text with skill/system reminders on
            # subsequent tool rounds. Find the original payload inside it.
            for match in re.finditer(r"\{", text):
                try:
                    value, _ = json.JSONDecoder().raw_decode(text[match.start():])
                    if isinstance(value, dict) and ("readiness_probe" in value or "call_context_token" in value or ("agent" in value and "skills" in value)):
                        return value
                except ValueError:
                    pass
    return {}


def choose_reply(body):
    tools = {item["function"]["name"]: item["function"] for item in body.get("tools", [])}
    messages = body.get("messages", [])
    payload = agent_payload(messages)
    called = [call for message in messages for call in message.get("tool_calls") or []]
    used = {call["function"]["name"] for call in called}
    if "agent" in payload and "skills" in payload:
        system = "\n".join(text for message in messages if message.get("role") == "system" for text in text_parts(message.get("content")))
        match = re.search(r"context_token=([A-Za-z0-9_-]+)", system)
        if not match:
            return None, "TEST 协议调用缺少本轮凭证。"
        token = match.group(1)
        source = next((f["id"] for f in payload.get("files", []) if f["name"] == "工作记录.xlsx"), None)
        if source:
            steps = [
                ("office_read_table", {"file_id": source}),
                ("office_validate_table", {"file_id": source, "rules": [{"column": "人员", "required": True}, {"column": "工时", "type": "number", "minimum": 0.0}]}),
                ("office_transform_table", {"file_id": source, "operation": "group", "columns": ["人员"], "aggregations": {"工时": "sum"}}),
            ]
            for name, arguments in steps:
                if name in tools and name not in used:
                    return name, {"context_token": token, **arguments}
            if "office_generate_document" in tools and "office_generate_document" not in used:
                call_names = {call["id"]: call["function"]["name"] for call in called}
                summary = None
                for message in messages:
                    if message.get("role") != "tool" or call_names.get(message.get("tool_call_id")) != "office_transform_table":
                        continue
                    raw = "\n".join(text_parts(message.get("content")))
                    for start in re.finditer(r"\{", raw):
                        try:
                            value, _ = json.JSONDecoder().raw_decode(raw[start.start():])
                            if isinstance(value, dict) and value.get("files"):
                                summary = value["files"][0]["id"]
                                break
                        except ValueError:
                            pass
                if summary:
                    return "office_generate_document", {"context_token": token, "title": "部门周报", "paragraphs": ["本文件由 TEST 协议服务驱动实际办公工具生成。"], "table_file_id": summary, "filename": "部门周报.docx"}
        return None, "TEST 协议任务完成，文件由实际办公工具生成；本次不包含真实模型推理。"
    if "StructuredOutput" in tools:
        control = "decision" in tools["StructuredOutput"]["parameters"].get("properties", {})
        if payload.get("call_context_token"):
            skills = ["pid-tuning", "control-safety"] if control else ["steam-system-monitoring", "control-safety"]
            loaded = {json.loads(call["function"]["arguments"]).get("name")
                      for call in called if call["function"]["name"] == "skill"}
            for name in skills:
                if "skill" in tools and name not in loaded:
                    return "skill", {"name": name}
            required = ["get_tuning_context"] if control else ["get_recent_window", "calculate_metrics", "detect_state"]
            for name in required:
                tool = "industrial_control_" + name
                if tool in tools and tool not in used:
                    return tool, {"call_context_token": payload["call_context_token"]}
        if control:
            if len(payload.get("history", [])) >= 3:
                output = {"schema_version": "1.0", "decision": "ABSTAIN", "delta": None,
                          "candidate_pid": None, "reason": "模拟验收已完成两个候选，停止继续提案。",
                          "expected_effect": "保留已评测的仿真记录。", "risk_notes": "仅用于部署验收。"}
            else:
                target = {"kp": 0.8, "ki": 0.06, "kd": 0.0} if len(payload.get("history", [])) < 2 else {"kp": 1.1, "ki": 0.2, "kd": 0.05}
                best = payload["current_best"]["pid"]
                output = {"schema_version": "1.0", "decision": "PROPOSE",
                          "delta": json.dumps({key: round(target[key] - best[key], 6) for key in target}),
                          "candidate_pid": json.dumps(target), "reason": "提出固定仿真候选以验证部署链路。",
                          "expected_effect": "由真实仿真程序评测四种工况。", "risk_notes": "模拟模型输出，不代表真实模型判断。"}
        else:
            assessment = payload.get("rule_assessment", {}).get("assessment", "NORMAL_STABLE")
            output = {"schema_version": "1.0", "assessment": assessment, "confidence": 1,
                      "needs_tuning": assessment in ("STEADY_HIGH", "STEADY_LOW"),
                      "manual_review_required": False, "evidence": [],
                      "reason": "按示例规则返回固定结果，验证部署与工具调用链路。", "warnings": []}
        return "StructuredOutput", output
    if "echo" in tools:
        return "echo", {"text": "ready"}
    if body.get("response_format"):
        return None, '{"status":"ready"}'
    return None, "服务连接正常。这是离线部署连通性测试。"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *args):
        pass

    def reply(self, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/__test__/stats":
            with LOCK:
                return self.reply({"model": "TEST STUB, not Qwen inference", "counts": dict(COUNTS),
                                   "trace": list(TRACE), "tool_results": list(TOOL_RESULTS.values())})
        self.reply({"object": "list", "data": [{"id": MODEL, "object": "model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        tool, value = choose_reply(body)
        with LOCK:
            COUNTS[tool or "text"] += 1
            call_id = "call_rehearsal_" + str(sum(COUNTS.values()))
            calls = {call["id"]: call["function"] for message in body.get("messages", [])
                     for call in message.get("tool_calls") or []}
            for message in body.get("messages", []):
                if message.get("role") != "tool":
                    continue
                call = calls.get(message.get("tool_call_id"), {})
                content = message.get("content", "")
                raw = content if isinstance(content, str) else json.dumps(content)
                failed = ("ripgrep execution failed" in raw or "TOOL_READ_FAILED" in raw
                          or raw.lower().startswith("error"))
                TOOL_RESULTS[message["tool_call_id"]] = {
                    "tool": call.get("name"), "success": not failed,
                    "skill": json.loads(call.get("arguments", "{}")).get("name"),
                }
                if call.get("name") == "industrial_control_get_tuning_context":
                    try:
                        context = json.loads(raw).get("data", {})
                    except (ValueError, AttributeError):
                        context = {}
                    TOOL_RESULTS[message["tool_call_id"]].update({
                        "context_keys": list(context),
                        "actual_pid": context.get("actual_pid"),
                        "best_pid": context.get("current_best", {}).get("pid"),
                        "history_iterations": [trial["iteration"] for trial in context.get("history", [])],
                    })
            TRACE.append({"roles": [message.get("role") for message in body.get("messages", [])],
                          "tools": [item["function"]["name"] for item in body.get("tools", [])],
                          "payload_keys": list(agent_payload(body.get("messages", []))),
                          "chosen": tool or "text"})
        message = {"role": "assistant", "content": value if not tool else None}
        finish = "stop"
        if tool:
            message["tool_calls"] = [{"id": call_id, "type": "function",
                                      "function": {"name": tool, "arguments": json.dumps(value, ensure_ascii=False)}}]
            finish = "tool_calls"
        result = {"id": "chatcmpl-rehearsal", "object": "chat.completion", "created": int(time.time()),
                  "model": MODEL, "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                  "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        if not body.get("stream"):
            return self.reply(result)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if tool:
            message["tool_calls"][0]["index"] = 0
        for delta, stop in [(message, None), ({}, finish)]:
            chunk = {**result, "object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": stop}]}
            self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4625)
    args = parser.parse_args()
    signal.signal(signal.SIGUSR1, lambda *_: os.execv(sys.executable, [sys.executable, *sys.argv]))
    print(f"TEST MODEL listening on 127.0.0.1:{args.port}; no real inference", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
