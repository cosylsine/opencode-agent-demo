"""Run an explicit authenticated ST smoke task against a deployed real model."""

import argparse
import getpass
import json
import os
import time
import uuid
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18101")
    parser.add_argument("--username", default="demo-admin")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait", type=int, default=1900)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--instructions")
    parser.add_argument("--retry-from", help="恢复失败任务的原始请求和已校验检查点")
    args = parser.parse_args()
    if args.retry_from and (args.source or args.instructions is not None):
        parser.error("--retry-from 使用原始输入，不能与 --source 或 --instructions 合用")
    password = os.environ.get("ST_SMOKE_PASSWORD") or getpass.getpass("登录密码：")
    args.output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=30) as client:
        auth = client.get("/api/v1/auth/me").raise_for_status().json()
        auth = client.post("/api/v1/auth/login", json={"username": args.username, "password": password},
                           headers={"Origin": args.base_url, "X-CSRF-Token": auth["csrf_token"]}).raise_for_status().json()
        until = time.monotonic() + args.wait
        while not any(m["ready"] for m in client.get("/api/v1/models").raise_for_status().json()["items"]):
            if time.monotonic() > until:
                raise RuntimeError("模型未就绪")
            time.sleep(2)
        source = args.source or Path(__file__).resolve().parents[1]/"examples/st_code/FB_Ratio.st"
        body = dict(schema_version="1.0", title="ST 代码优化真实模型验收",
                    filename=source.name, dialect="generic", actions=["comment", "test", "review", "fix"],
                    code=source.read_text(),
                    instructions=args.instructions if args.instructions is not None else "" if args.source else "工程约定：扫描周期 100 ms。正常 Enable=TRUE 且 Denominator 非零时，Ratio 为比值且 Valid=TRUE。"
                    "Enable=FALSE 或 Denominator=0 时，必须 Ratio=0.0、Valid=FALSE，禁止保留上周期有效状态。"
                    "请按上述明确约定修复，覆盖连续扫描、零除数和禁用后的回归；接口保持不变。")
        if args.retry_from:
            previous = client.get("/api/v1/st-runs/" + args.retry_from).raise_for_status().json()
            body = dict(previous["request"], retry_from=args.retry_from)
        run = client.post("/api/v1/st-runs", json=body, headers={"Origin": args.base_url,
                          "X-CSRF-Token": auth["csrf_token"], "Idempotency-Key": str(uuid.uuid4())}).raise_for_status().json()
        print("run_id=" + run["run_id"], flush=True)
        previous = None
        while True:
            run = client.get("/api/v1/st-runs/" + run["run_id"]).raise_for_status().json()
            if run["status"] != previous:
                print("status=" + run["status"], flush=True)
                previous = run["status"]
            if run["status"] not in ("queued", "running"):
                break
            if time.monotonic() > until:
                raise RuntimeError("等待模型超时，任务保留，可从页面继续查看。")
            time.sleep(2)
        (args.output/"real-agent-result.json").write_text(json.dumps(run, ensure_ascii=False, indent=2))
        if run["status"] != "completed":
            raise RuntimeError(run["error"])
        for artifact in run["artifacts"]:
            assert Path(artifact["name"]).name == artifact["name"]
            assert artifact["download_url"].startswith(f'/api/v1/st-runs/{run["run_id"]}/artifacts/')
            response = client.get(artifact["download_url"]).raise_for_status()
            (args.output/artifact["name"]).write_bytes(response.content)
        print(json.dumps(dict(model=run["model_id"], checks=run["checks"], bugs=len(run["result"]["bugs"]),
                              tests=len(run["result"]["tests"]), artifacts=len(run["artifacts"])), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
