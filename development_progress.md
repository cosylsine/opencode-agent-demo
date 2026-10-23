# 首版 Demo 开发进展

## 2026-09-24 独立回路进程

应用户纠正，示例回路已从 demo-backend 生命周期中拆出。`examples.live_process` 单独持有采样锁、每秒写入 JSONL；后端只读，并仅在回路运行且对应工况已有连续 120 秒的 121 点窗口时接受新检查。停机缺口不回填，旧版后端生成的记录不计入新窗口。两个进程可独立启动和停止；备份需同时停止两者。本节覆盖下文早先关于“回路随后端运行”的描述。

## 2026-09-24 持续仿真与多轮调参

四个虚拟工况每秒分别向 `DEMO_STATE_DIR/telemetry/*.jsonl` 追加状态；Monitor 的 121 点冻结窗口直接取自记录。新增 `coupled_load` 工况和版本 2 离线试验曲线，Control 请求中提供冻结状态、指标、当前最佳与试验历史，`pid-tuning` skill 记录小步调参经验。模型解释字段改为简体中文并在真实 OpenCode 边界校验；针对当前模型的嵌套工具参数字符串做传输层适配，公共契约与 Guard 仍保持强类型。停机备份现在包含时序 JSONL。

本机真实 OpenCode/MCP 冒烟测试通过 `normal` 与 `improvable`；另一次 `coupled_load` 真实运行经四轮均改善的离线候选达到目标，候选依次为 `(1.05,0.15,0.03)`、`(1.1,0.15,0.03)`、`(1.1,0.2,0.03)`、`(1.1,0.2,0.05)`，运行 ID `440dcb24-b006-49e0-9ce9-c10531109127`。两个角色的公开解释均为中文。后端与 OpenCode user services 已重启，健康接口 ready，JSONL 已核对持续增长。

## 2026-09-24 对话入口扩展

已完成独立的 reception Agent、持久化会话与消息 API、确定性检查意图路由、对话内公开运行轨迹和桌面/手机双布局；原运行控制台保留。`scripts/check.sh` 通过 77 项 Python 测试、Ruff、前端生成/类型检查/构建；Playwright 4 项通过，包括普通对话、关键词及显式按钮触发、重载恢复、控制台曲线和 390/320px 无横向溢出。隔离 OpenCode 权限探针确认 reception 无业务工具权限；本机真实模型文本问答和正式 API 关键词触发 Monitor 已通过。用户服务已切换为项目 `719agent-demo-opencode.service` 并重启后端，健康接口 ready，数据库迁移到 0004。


基线：`development_constraints.md` V1.0，按 D0 → D1 → D2 → D3 → D4 推进。
验收口径：代码实现、Fake 测试、真实集成、部署验收分别记录，不能互相替代。

| 阶段 | 当前状态 | 证据 / 待办 |
| --- | --- | --- |
| D0 外部依赖 | 环境 / OpenCode / MCP / 本机 vLLM 已实测 | 1.18.31 的实际 /doc 已导出；双 Agent 与工具调用通过；真实推理中网络超时/abort 时序专项复验待补 |
| D1 契约与数据 | 最小验收通过 | 14 项测试通过；模型、Schema、fixtures、固定示例、指标、Guard、SQLite/Alembic 已实现 |
| D2 单轮链路 | 代码、Fake 与本机真实模型验收通过 | `real-integration.json` 记录正常场景无需调参、可改进场景一轮候选评测成功；双角色 MCP 读取有审计 |
| D3 多轮恢复 | 确定性 / Fake 自动化验收通过 | 最多 5 轮、幂等、取消、超时、状态与文件崩溃点恢复、一次接受、未知结果停止；真实模型故障行为仍依赖 D0/D2 |
| D4 本机交付 | 回环部署 / 浏览器 / 备份恢复通过；对外发布待配置 | 当前三服务手动运行在回环 8000/14096/18000；生产身份/CSRF 单测通过；外部 TLS/认证/网段尚未验收 |

## 2026-09-22

- 已逐节读取两份规格，确认实现约束优先于架构中旧默认值。
- 环境核验命令：`python --version`、`node --version`、`npm --version`、`/data9/xjx/.opencode/bin/opencode --version`、`serve --help`、`df -T .`、`systemctl --user is-system-running`。
- 尚未修改已有 OpenCode 配置/凭据，未宣称模型或部署已通过。

### D0 / D1 阶段证据

- `python scripts/probe_opencode.py`：1.18.31 独立实例通过 health/Agent/空 Session abort，导出实际 /doc（162 路径）。详见 `docs/runbooks/opencode-compatibility.md`。
- `.venv/bin/python scripts/generate_fixtures.py`、`.venv/bin/python scripts/export_contracts.py`：成功。
- `.venv/bin/pytest tests/test_domain.py -q`：**14 passed**，含文档全部候选数值、严格类型、规范化 hash、快照及文件损坏。
- D0 模型调用验收尚未通过；按文档允许推进确定性代码，不将其标记为真实集成。

### D2 / D3 阶段证据

- 首次单轮集成：`pytest tests/test_workflow.py -q`，10 passed（Fake 明确注入，真实 Example 子进程与 SQLite）。
- 当前 `pytest -q`：**59 passed**。覆盖正常、改善、恶化、硬边界、ABSTAIN、Monitor 冲突/低置信度、格式修复/候选重提预算、3 轮无改善/5 轮上限/900 秒截止、取消、同键冲突/同场景合并、15 个状态提交点重启、文件回收/损坏、未知请求不重发、作用域/认证/CSRF、Adapter 替换和备份还原。
- `ruff check .` 通过；前端 OpenAPI 类型生成、TypeScript 与 Vite 首次构建通过。构建后补齐依赖修复，最终版本待复验。
- 默认最大轮数已从 D2 的 1 改为 D3 的 5；测试仍显式冻结各场景的轮次预算。
- 模型未配置时 readiness=false，触发返回 503；测试 Fake 不作为服务运行选项。
- D2 的真实 OpenCode 双 Agent/MCP 端到端和 D4 的外部 TLS 访问验收仍未完成，不将自动化结果代替它们。

### 最终本机交付记录

- `bash scripts/check.sh`：**72 passed**；Ruff 通过；Schema/OpenAPI 与前端类型重建成功；TypeScript 和 Vite 6.4.3 构建通过。两条测试依赖弃用提示不影响结果。
- `npm run test:browser --prefix apps/dashboard`：**2 passed**。真实 HTTP API + SQLite + Example 子进程 + 测试 Fake Agent 的页面触发、曲线和建议展示通过；正式服务的模型缺失状态与禁用触发也通过。截图：`docs/runbooks/dashboard-fake.png`（明确 Fake 验收）、`dashboard-degraded.png`。
- `npm audit --prefix apps/dashboard --json`：**0 vulnerabilities**，汇总见 `docs/runbooks/frontend-audit.json`。前端固定 ECharts 6.1.0 / Vite 6.4.3 / React 19.1.0；构建按图表/渲染器/React 分包。
- `python scripts/probe_agents_mcp.py`：实际 OpenCode 加载两 Agent/三个 Skill，MCP connected，stdio 握手与四工具发现通过；无效服务凭据被拒绝。验证合并权限的最后匹配结果为默认 deny、角色工具 allow。**没有真实模型调用证据**。
- 新增 `0002` 迁移，数据库额外保证每 run 唯一基线、每 iteration 唯一候选 job、轮次编号唯一，已测试重复插入被拒绝。
- 已创建项目专用 `deploy/runtime/runtime.env`（0600，不含模型凭据，未进版本控制），未读取/修改原 OpenCode 凭据。
- 已启用 `719agent-demo-backend.service`；`systemctl --user is-active` 返回 active，`loginctl show-user xjx -p Linger` 返回 yes；只监听 **127.0.0.1:18000**，未改已有 4096/8000 服务。
- `python scripts/verify_local_deployment.py`：重启后 API 与历史查询正常，停机备份、恢复及文件哈希校验通过，记录见 `docs/runbooks/local-deployment.json`。正式历史目前为空；**非空历史恢复由自动化测试验证**，不混同证据。
- 健康状态：live=true、ready=false、degraded=true，原因 `MODEL_NOT_CONFIGURED`；数据库迁移 0002、磁盘、Example 观测源正常。OpenCode 常驻 unit 尚未启用，避免无模型配置的失败重启。
- README 已交付安装、启动/停止/重启、模型配置、演示、测试、日志、备份恢复、Adapter 接入与对外部署步骤。API 不向页面提供文件路径/服务 Token/模型凭据。

## 2026-09-23 本机真实模型复验

- `runtime.env` 已切到独立 vLLM：GPU 0、1 上的 `Qwen3.6-35B-A3B`，OpenCode 1.18.31 通过项目 provider 调用；`scripts/probe_vllm.py` 四项模型接口探针通过。
- 适配器对 OpenCode 的 `StructuredOutput` 工具提交方式和结构化用户消息读取 400 做了限定兼容；按 Session 派生 `messageID`，不重发不确定请求。一次 Control 的嵌套 PID JSON 字符串在严格契约校验前受限解码。细节见 `docs/runbooks/opencode-compatibility.md`。
- `scripts/doctor.py --model-probe` 与后端健康接口均返回 ready。`scripts/probe_agents_mcp.py` 复验通过；`scripts/smoke_real.py` 收紧为要求实际候选评测后，记录 `normal=NO_TUNING_REQUIRED`、`improvable=SUCCEEDED/TARGET_REACHED`，证据见 `docs/runbooks/real-integration.json`。
- `bash scripts/check.sh`：74 项 Python 测试通过，Ruff、TypeScript 与 Vite 构建通过；更新后的浏览器用例 2 项通过，正式页面就绪显示与健康接口一致。
- 当前 vLLM、OpenCode、后端分别监听回环 8000、14096、18000；后端为手动进程，user systemd 服务未用于这轮真实模型验收。
- 第一次业务复验曾因 Control 输出形状和读取超时转人工；该结果未计入成功证据。真实网络中断和推理中 abort 仍需专项复验。

## 待补信息与未完成验收

当前可独立完成的工程代码与本机验证已落地，**首版 V1 不能标为全部完成**：

1. **真实模型异常时序**：provider/model 和双角色实际调用已通过本机验收；还需用真实推理分别验证网络超时后的找回和推理中的 abort/停止确认。自动化测试已覆盖不确定请求不重发，但不代替实测。
2. **对外发布**：需要域名/IP、允许访问网段、TLS 证书路径和账户配置。本机无 Nginx 可执行文件；已提供配置模板，尚未安装/修改系统代理。需完成外部客户端 HTTPS、未认证/CSRF 拒绝及内部路径不可访问的真实部署检查。
3. 上述真实环境检查通过后，更新此文档与 `docs/runbooks/test-matrix.md`，再对照两份规格勾选 V1 完成条件。Fake、空 Session abort、仅 MCP 握手及回环服务都不能代替这些检查。

## 当前入口

- 页面：`http://127.0.0.1:18000`（服务器回环；远程 IDE 可转发 18000 端口）。
- 运行手册：`README.md`。
- 兼容性证据：`docs/runbooks/opencode-compatibility.md`、`d0-probe.json`、`agent-mcp-probe.json`。
- 用例映射：`docs/runbooks/test-matrix.md`。
