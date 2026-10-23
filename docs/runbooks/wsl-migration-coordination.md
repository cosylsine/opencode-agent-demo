# WSL 迁移协调与远端维护

## 部署约定

- WSL 项目：`/home/xjx/opencode-agent-demo`，全新业务状态，不导入远端历史。
- 新页面：`http://100.64.0.17:18080`。Windows Tailscale Serve TCP 转发到 WSL 的 `127.0.0.1:18080`。
- WSL 后端 `127.0.0.1:18000`、OpenCode `127.0.0.1:14096`、MCP 和独立采样器均在 WSL。
- 模型：server9 的 GPU 0、1，`qwen3.6-35b-a3b`，现有 vLLM `127.0.0.1:8000`。
- 模型网关：`http://100.64.0.6:18084/v1`，独立 Bearer 密钥。
- LycheeRAG 的 `18082/18083` 模型服务及 `5173` 页面转发不属于本次迁移。

## 远端网关交付

本次 WSL agent 可通过已有 Windows SSH 凭据操作 server9，已负责部署以下文件；后续 agent 先核查状态，勿重复生成密钥或启动第二份实例。

- 源码：`/data9-2/xjx/opencode-agent-demo/scripts/model_gateway.py`。
- unit：`719agent-demo-model-gateway.service`，使用远端项目原有 `.venv`。
- 密钥文件：`/data9-2/xjx/opencode-agent-demo/deploy/runtime/model-gateway-key`，权限 0600。
- 上游固定 `http://127.0.0.1:8000`，网关仅绑定 `100.64.0.6:18084`。
- 仅允许 `GET /v1/models` 与 `POST /v1/chat/completions`；支持 SSE、工具调用和 JSON Schema 透传。缺失或错误密钥返回 401，其他路径 404，上游无法连接返回 502。
- 密钥已通过 SSH 传入 WSL 的私有 `deploy/runtime/runtime.env`，变量名 `DEMO_MODEL_API_KEY`。不要在文档、终端输出或日志中打印密钥。

远端核查命令：

```bash
systemctl --user status 719agent-demo-model-gateway.service
ss -lntp '( sport = :8000 or sport = :18084 )'
curl --noproxy '*' -i http://100.64.0.6:18084/v1/models  # 预期 401
```

带鉴权检查应由脚本读取密钥文件并构造请求头，不将密钥展开到命令行参数。WSL 的 `scripts/probe_vllm.py` 会从环境读取它。

## 模型生命周期

迁移沿用原模型进程，没有重启或改变 GPU、模型参数和监听地址。迁移前确认该进程处于登录 session 中，并非独立 systemd 模型服务；主机重启后不会由本项目自动恢复模型。

若模型确已退出，先确认端口 8000、GPU 0/1 空闲，再执行原有启动脚本：

```bash
cd /data9-2/xjx/opencode-agent-demo
bash scripts/launch_vllm_gpu01.sh
```

脚本使用 `/data9/HF-model/Qwen3.6-35B-A3B` 和 `/data9/whp/.conda/envs/openhands-py2c/bin/vllm`；加载与编译可能需要十多分钟。不要停止其他 GPU 上的任务。模型 systemd 托管改造不属于此次迁移。

## 切换与回退

只有在 WSL 验收证据确认通过后，才停用旧的四个业务 unit：

```bash
systemctl --user disable --now \
  719agent-demo-tailnet.service 719agent-demo-backend.service \
  719agent-demo-opencode.service 719agent-demo-loop.service
```

不要使用 `719agent-demo-*` 通配符停服务，以免关闭新的模型网关。保留远端源码、原配置、`var/backend` 与 `var/opencode`，作为原实例回退材料。

如果需要回退，重新启用这四个旧 unit，旧入口仍为 `http://100.64.0.6:18080`。WSL 和远端业务数据独立，回退不会自动合并 WSL 新数据。恢复采样后需等待连续 120 秒窗口。

## 验收与回填

最终状态及测试结果记录在同目录 `wsl-migration-status.md`。远端 agent 后续若改动模型网关，需回填 unit 状态、实际监听地址、模型 ID、鉴权拒绝测试、流式与工具调用测试、模型进程是否重启，以及密钥文件位置；不要回填密钥值。
