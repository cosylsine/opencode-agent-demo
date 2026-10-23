# OpenCode 兼容性核验

## reception 文本会话补充（2026-09-24）

新增 `reception` 自定义 Agent，项目隔离探针确认其存在且业务工具权限均为 deny。每轮普通问答创建独立 Session，携带最近 12 条持久化消息与最近一次 run 的公开快照；使用固定 messageID 验证返回的 Assistant role、sessionID、parentID、agent 和完成时间。客户端不重发结果未知的 Prompt。真实 `local-vllm/qwen3.6-35b-a3b` 普通问答已返回文本；正式 API 的“请检查 PID 状态”创建 `normal` run，Monitor 最终给出 `NORMAL_STABLE/NO_TUNING_REQUIRED`。隐藏推理不进入业务 API。

2026-09-22，实测二进制 `/data9/xjx/.opencode/bin/opencode` 版本 **1.18.31**。

已运行 `python scripts/probe_opencode.py`：独立 XDG 目录、项目配置、随机端口、Basic Auth、`--pure`、默认全部 deny；不读取用户凭据。实际 `/doc` 导出为 `docs/api/opencode-1.18.31.openapi.json`（162 个路径），原始脱敏结果在 `d0-probe.json`。探针进程已关闭。

本机 4096/8000 已被其他进程监听，项目默认使用 14096/18000；不操作其他服务。

| 项目 | 实际结果 |
| --- | --- |
| health / 自定义 Agent | 版本正确，probe Agent 存在 |
| 结构化字段 | 实际 /doc 支持 format=json_schema、retryCount；AssistantMessage 有 structured、parentID、time.completed、finish |
| 自定义 messageID | /doc 规定 `^msg`；尚未通过模型实测，业务客户端暂不提交自定义 ID |
| 消息分页 | 实际 /doc 有 limit/before；客户端遍历分页并校验唯一用户消息、session、parentID、agent、完成字段 |
| 空 Session abort | 返回 true；随后 status 中 Session 不存在（idle） |
| 工具中间消息 / 最终 structured | **未验收：缺少明确的 provider/model 配置** |
| HTTP 超时后找回结果 | **未验收：需真实推理探针** |
| 推理中 abort 与停止确认 | **未验收**，空 Session 的结果不能代替 |
| 只读 MCP / 默认拒绝越权 | 等待业务 MCP 与真实模型联合验证 |

参考：[官方 Server](https://opencode.ai/docs/server/)、[权限规则](https://opencode.ai/docs/permissions/)。本地 /doc 优先于在线文档；不依赖 messageID 提供服务端幂等，每次尝试独立 Session 且仅一条业务用户消息。

## 业务 Agent 与 MCP 补充实测

`python scripts/probe_agents_mcp.py` 使用真实 1.18.31 临时服务，成功加载 Monitor / Control 及三个 Skill，`/mcp` 返回 connected；官方 Python MCP SDK 完成 stdio 握手并发现恰好四个工具。无效服务凭据的只读调用被后端拒绝。结果见 `agent-mcp-probe.json`。

已检查实际合并后的权限列表：OpenCode 内置初始 allow 规则之后存在项目和 Agent 的 deny，按最后匹配规则判断，bash/edit/read/task/webfetch/websearch/未知工具均为 deny；角色自身的业务工具为 allow，另一角色工具为 deny。OpenCode 自动加入的输出目录访问规则不会覆盖 read 的 deny。此检查验证生效配置，不等于已经用真实模型完成越权尝试。

后台新调用的 readiness 也执行这一权限检查；权限不符时降级，不扩大授权。结构化输出内部工具及模型调用的实际行为仍保留为待验收。

## 内置免费模型配置与实测（2026-09-22）

- 已创建私有 `deploy/runtime/provider.json`，内容为 `{"provider":{"opencode":{}}}`，权限 0600；运行环境保留原服务凭据。
- 运行服务刷新后的 `/provider` 不再包含 `mimo-v2.5-free`，旧列表检查不足以证明可调用。改用列表中输入/输出费用均为 0 的 `opencode/mimo-v2.6-flash-free`，同步运行环境、示例与 README。
- OpenCode 和后端已通过项目 systemd user unit 启动，监听 14096/18000；`/global/health` 成功，业务 MCP 为 connected。
- 真实普通文本探测收到 AssistantMessage APIError，provider 状态码 403：`OpenCode's free tier can only be used from within OpenCode`。请求确实经本机 OpenCode 1.18.31 发出，不能据此认定模型推理已通过。上游有[同版本报告](https://github.com/anomalyco/opencode/issues/49588)及[自定义 Agent 报告](https://github.com/anomalyco/opencode/issues/50081)。
- 结构化消息读取还复现 HTTP 400 `Expected OutputFormatJsonSchema`，与[上游问题](https://github.com/anomalyco/opencode/issues/26929)一致；现有 readiness 因此可能只显示 RESULT_UNKNOWN，掩盖具体模型错误。
- 当前模型配置文件齐全，但免费 provider 拒绝调用，端到端验收仍未通过。没有放宽 Agent 权限或切换到 Fake。

## 运行中服务复测（2026-09-23）

- 手动启动的后端和 OpenCode 分别监听 `127.0.0.1:18000`、`127.0.0.1:14096`。后端健康接口中 database、disk、observation 为 ready；OpenCode `/global/health` 返回 1.18.31，`/mcp` 中 `industrial_control` 为 connected。当前 `runtime.env` 已设置 provider/model 和两项本地服务凭据。
- 加载 `runtime.env` 后运行 `scripts/doctor.py --model-probe`，`real_model_probe.ready=false`、`error_code=RESULT_UNKNOWN`。查看最新 readiness Session 的 `/session/{id}/message`，HTTP 400，错误以 `Expected OutputFormatJsonSchema` 开头。这个读取问题发生在 OpenCode HTTP 层，已有[同类上游报告](https://github.com/anomalyco/opencode/issues/26929)。
- 另建独立诊断 Session，不使用 `format`，向本项目 `monitor` Agent 的 `opencode/mimo-v2.6-flash-free` 发送单条 `Reply only OK; do not call tools` 文本。`POST /session/{id}/message` 返回 HTTP 200，但 AssistantMessage 为 `APIError`，其中 provider 状态码 403，消息为 `OpenCode's free tier can only be used from within OpenCode`。该普通文本 Session 的 `/message` 可正常读取（HTTP 200），进一步确认前述 400 与结构化格式有关。所以当前免费模型在本项目的自定义 Agent 路径下无法完成推理；HTTP 服务与 MCP 的健康状态不能代替模型就绪。
- 结论：接入 vLLM 或其他 API 时，需要分别验证 provider 能完成普通文本与业务工具调用，以及带 JSON Schema 的 Session 消息可被读取并正确关联。更换 provider 本身不能证明 400 读取问题已经消失；在这些验收通过前，后端继续保持 degraded，不放宽权限或使用 Fake 冒充真实调用。

## 本机 GPU 0、1 vLLM 试接（2026-09-23）

- 初次排查时，GPU 0、1 各为 NVIDIA RTX A6000，已有另一用户的 vLLM 工作进程，各占约 44 GB；服务在 `127.0.0.1:8000/v1` 可访问，`/version` 为 0.25.1，`/v1/models` 返回 `qwen3.6-35b-a3b`。当时未重启或修改该共享服务，也未再启动占显存的实例。
- 初次直接请求 `/v1/chat/completions`：普通文本和 `response_format=json_schema` 均返回 HTTP 200 且有有效内容；`tool_choice=auto` 和指定 `echo` 工具均返回 HTTP 400，提示需要 `--enable-auto-tool-choice` 与 `--tool-call-parser`。当时 `ready_for_agents=false`。Qwen3.6 [官方模型卡](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)建议 vLLM 工具调用使用 `--tool-call-parser qwen3_coder`。
- 新增项目 provider 配置 `deploy/provider.vllm-gpu01.example.json`，私有副本在 `deploy/runtime/provider.vllm-gpu01.json`。隔离 OpenCode 1.18.31 实例能加载该 provider；继承的代理环境最初使本机模型请求返回 502。启动器现为 `127.0.0.1` 与 `localhost` 补充 `NO_PROXY/no_proxy`，隔离实例的普通文本请求随后成功。
- 隔离实例的 JSON Schema 请求返回 `StructuredOutputError: Model did not produce structured output`；其消息中可见模型写出了 `StructuredOutput` 的文本形式，却没有可解析的工具调用。随后 `/session/{id}/message` 仍返回 HTTP 400。工具解析器启用后需重新验证结构化输出，并单独解决消息读取问题。现有手动启动的项目服务和 `runtime.env` 未切换到 vLLM。
- 后续用 `scripts/launch_vllm_gpu01.sh` 在 GPU 0、1 启动独立服务，使用可访问的 `/data9/whp/.conda/envs/openhands-py2c`（vLLM 0.25.1）。首次启动在权重加载完成后因 `ninja` 不在 `PATH` 而退出；脚本现已加入 conda 环境 `bin`，FlashInfer JIT 预检通过。再次启动后，`scripts/probe_vllm.py` 的普通文本、JSON Schema、自动工具调用和指定工具调用全部通过，`ready_for_agents=true`。尚未据此证明 OpenCode Agent 的端到端调用成功。
- 对该结构化 Session 逐条读取消息时，`GET /session/{id}/message/{assistantID}` 返回 200，但用户消息的单条读取仍返回 400；`/api/session/...` 的另一套接口也不能读取这些旧格式消息。因此当前没有经过验证、又能在响应丢失后安全找回完整业务结果的 HTTP 替代路径，不能为了让页面显示 ready 而跳过消息关联检查。

## vLLM 双 Agent 端到端复验（2026-09-23）

- 项目 `runtime.env` 已切到 `local-vllm/qwen3.6-35b-a3b`，OpenCode `/provider` 中该模型的 context/output 限额为 65536/8192。独立 vLLM 已开启 `qwen3_coder` 工具解析与非思考模式；`scripts/probe_vllm.py` 的文本、JSON Schema、自动和指定工具调用均通过。
- OpenCode 的结构化格式要求调用 `StructuredOutput` 工具。模型只返回 JSON 文本时，AssistantMessage 报 `StructuredOutputError`；明确要求调用该工具后，实测 `structured` 字段有效、`finish=tool-calls`，工具状态为 `completed`。`retryCount=2` 的单独探测仍显示 `retries=0`，与[上游问题](https://github.com/anomalyco/opencode/issues/25430)一致。客户端因此明确引导最终工具调用，仍严格验证结构化契约。
- OpenCode 1.18.31 对包含结构化 UserMessage 的消息列表仍返回 HTTP 400。现由唯一 Session ID 派生固定 `messageID`，POST 和恢复路径只读取最新一条 AssistantMessage，并验证 `sessionID`、`parentID`、角色、完成时间、结构化工具状态和契约。模拟 POST 读超时的测试证明只发一次请求并从最新消息恢复；真实推理中网络超时与 abort 时序仍待专项复验。
- `qwen3_coder` 的一次 Control 结果将 `delta` 和 `candidate_pid` 两个嵌套 PID 对象编码成 JSON 字符串。适配器仅对这两个字段做长度受限的解码，随后运行原有严格 `ControlOutput` 校验；非法数值仍拒绝。提示词同时明确要求提交对象。
- `scripts/doctor.py --model-probe` 返回 `ready=true`，后端 `/api/v1/health` 返回 `ready=true, degraded=false`。`scripts/probe_agents_mcp.py` 复验 Agent 权限、MCP 连接和无效凭据拒绝通过。
- 收紧后的 `scripts/smoke_real.py` 要求真实 Control 候选被评测。最终 `docs/runbooks/real-integration.json` 记录 `normal=NO_TUNING_REQUIRED` 和 `improvable=SUCCEEDED`，后一场景一轮达到 `TARGET_REACHED`；Monitor 三项只读工具和 Control `get_tuning_context` 均有 MCP 审计。此前一次可改进场景因结构化 PID 字段及结果读取超时转 `MANUAL_REVIEW_REQUIRED/RESULT_UNKNOWN`；该失败没有被算作最终通过证据。
