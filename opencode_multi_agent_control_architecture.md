# 基于 OpenCode 的蒸汽动力系统 PID 智能优化系统

## 系统架构与实施计划（本机示例 Demo 版）

> 文档状态：当前 B 回路实现说明；下文另存 V1.1 历史设计  
> 修订日期：2026-09-28  
> 适用项目：719agent 横向工程项目  
> 当前交付：在本机运行、可通过业务网页访问的 OpenCode 双 Agent Demo  
> 核心范围：历史 V1.1 示例设计。当前实现另有独立汽水循环 B 简化仿真和调优流程，见下文“当前实现”。不接真实控制系统。

### 当前实现：Monitor 与 B 回路 Control 已解耦

`EXAMPLE` 虚拟压力回路只供 Monitor 冻结 120 秒窗口并诊断；异常时返回“建议人工发起仿真调优”，不再自动调用 Control。操作员通过独立的 `POST /api/v1/tuning-runs` 启动 `SIMULATION` 回路 B。B 回路使用约 500 kW / 3.26 t/h / 2.5 MW 的简化汽水循环模型，同一组给水泵 PID 在负荷上升、下降、斜坡和多次升降四种变化中接受安全检查与评分。Control Agent 提出完整参数，编排器裁决并保存每个工况的事件、响应和候选历史；没有真实参数下发。详见 [README 的 B 回路说明](README.md#b-回路模型与接口)。下文 V1.1 的 Monitor→Control 自动流程、小步界限与“无模拟器”描述仅供历史设计参考。

B 回路的操纵量是给水泵转速，泵 PID 跟踪给水目标流量；阀门、燃烧与汽轮机调节策略固定。汽包水位、蒸汽压力、汽轮机功率用于系统响应和验收。模型按 1 秒步长保存水、蒸汽、凝汽库存与时序，具有版本化的演示动态常数、安全界限和 500 kW 名义平衡点。每组参数都从相同初始状态跑四个工况；任一工况越界即不接受。合规候选的评分按流量 40%、水位 20%、压力 20%、功率 10%、泵速变化 10% 加权，再用平均分 70% 和最差工况 30% 排名。模型尚未用现场数据校准。

> 实施更新（2026-09-24）：下文保留原 V1.1 设计基线。当前代码已将示例观测升级为独立于后端启停的虚拟压力回路，按秒写入 JSONL；后端在检查请求到来时冻结此前连续 120 秒窗口。Control 读取观测状态和调参 skill，在更复杂的 `coupled_load` 工况中逐轮提交受限候选到离线模拟器。候选仍不会应用到持续采样回路或真实设备。实际运行方式与版本以 [README](README.md) 为准。

开发时配合阅读 [Demo 开发约束与实施顺序](development_constraints.md)。该文档补齐状态裁决、数据契约、可达示例、观测/PID 快照及 OpenCode 请求恢复，并明确首版的简化项与实际开发顺序；涉及这些实施细节时以开发文档为准。

---

## 历史 V1.1 设计基线（以下不描述当前 B 回路行为）

## 0. 阅读方式与本次范围调整

### 0.4 对话入口扩展（2026-09-24）

工作台增加面向用户的 `reception` Agent。它只负责普通问答和解释本演示的操作边界，没有 MCP、文件、网络或控制权限。浏览器仅调用 Orchestrator 的对话 API；后端为每轮对话创建隔离 OpenCode Session，携带最近对话摘要并保存消息，绝不向浏览器暴露 OpenCode 凭据。对话失败要显示明确错误，不能伪装为模型回复。

显式检查按钮直接走现有 Trigger API。消息同时包含检查动作词（检查、检测、诊断、分析、排查、评估）及对象词（PID、压力、状态、系统、回路、参数）时，由 Orchestrator 确定性识别并创建 run；接待 Agent 不自行调用 Monitor，也不解释为授权下发。若仅讨论这些词而未请求检查，作为普通问答。关键词命中时记录用户原文、触发来源、run ID，展示 run 状态、Monitor 理由、Control 建议与事件摘要，并可转到原控制台查看曲线和完整证据。每回路单活动 run、同场景合并、跨场景冲突及就绪约束沿用既有机制。

页面采用同一套 Signal Console 色彩和组件：对话为主工作区，运行控制台保留历史、曲线、复核和定时配置。桌面使用会话侧栏与对话区，窄屏改为纵向并保持输入框、快捷检查和控制台入口可见。模型内部不可公开的隐藏推理不展示；展示可审计的业务状态、Agent 公开输出和工具/执行事件。

本文同时作为架构说明和后续开发计划。除明确标为“后续”的内容外，V1 均指本次示例 Demo；未来内网生产要求不得提前变成 Demo 的阻塞项。

### 0.1 项目意图

建设蒸汽动力系统给水泵 PID 优化的**监督与建议层**：Monitor Agent 分析状态，Control Agent 提出有界小步候选，确定性程序校验、试算、评估并保存建议。底层实时 PID 控制、保护和联锁仍由控制系统承担。当前重点验证 Agent 协作、工具调用、可解释迭代、持久化、恢复和网页展示；示例结果不构成实际控制效果的证明。

### 0.2 相对 V1.0 的变更

| 项目 | 本版实施决定 | 后续演进 |
| --- | --- | --- |
| 被控对象 | 本地固定数据 + 确定性示例程序，无设备连接 | 内网数据采集 Adapter |
| 候选评测 | `CandidateEvaluator` 接口 + `ExampleEvaluator` | 经验证的模拟器/离线评测 Adapter，按需接入 |
| 真实下发 | 不实现、不注册路由、不暴露 MCP 工具 | 独立审批与控制网关，不能直接替换评测器实现下发 |
| Control 职责 | 仅输出一个候选；编排器负责提交评测 | 保持这一职责边界 |
| 数据库 | SQLite，本机单进程业务服务 | 有多实例需求时迁移 PostgreSQL |
| 模型 | 通过 OpenCode 接入已配置且可访问的 provider | 内网部署时换为内网模型服务 |
| 部署 | 本机 OpenCode + 后端 + 静态网页 + 反向代理 | 断网安装包和内网部署另行验收 |
| 状态识别 | 确定性规则，无须训练模型 | 小型网络、事件触发按实际数据推进 |

### 0.3 不可偏离的架构结论

- OpenCode 是 Agent 推理运行时，外部 Orchestrator 是工作流编排器；不引入 Supervisor，不依赖 Agent 自由对话或 `task` 编排。
- 数据库是唯一业务状态源；OpenCode Session 是推理上下文和审计线索。
- Agent 给建议，Guard 和 MetricsEvaluator 做裁决；所有边界、权重、阈值来自版本化配置。
- 每回路同一时刻最多一个活动 run；每轮最多一个已登记候选和一个有效候选评测结果。
- 本版接受候选仅更新 run 内的 `best_pid`，不修改设备参数，也不将建议当成当前实际 PID。
- 示例、未来仿真、真实观测必须通过 `source_kind` 区分，不能混用评分或将示例结果表述为现场效果。
- OpenCode 版本差异封装在 `OpenCodeClient`；以部署版本实际 `/doc` 和兼容性测试为准。

---

## 1. 业务背景与已知环境

### 1.1 业务对象

目标是在变工况、稳态偏差、超调、振荡或响应迟缓时辅助优化给水泵 `Kp`、`Ki`、`Kd`，使出口蒸汽压力接近配置目标，示例默认目标为 `30`。真实压力单位、PID 形式、采样周期和参数量纲尚待控制专业人员确认，不得根据数字 `30` 推定单位或生产阈值。

关注变量包括压力、目标值、给水流量、温度、负荷、工况、PID、控制输出、饱和状态、数据质量和时间戳。Demo 仅生成流程所需字段，其他字段允许显式缺省；缺省字段不能伪造成实测值。

前期方案已探索 Skill 提供调参经验、Agent 调用工具取得响应、按指标迭代的方式。本版将这一流程工程化，但暂不要求原有模拟器。项目与 DCI-Bench 等科研任务相互独立。

### 1.2 本机核验结果

2026-09-22 已通过只读命令核验：

- 工作目录：`/data9-2/xjx/opencode-agent-demo`；当前仓库内容为本架构计划文档，尚无应用代码。
- OpenCode 安装目录：`/data9/xjx/.opencode`。
- 实际可执行文件：`/data9/xjx/.opencode/bin/opencode`。
- `opencode --version` 返回 `1.18.31`；安装目录内 `@opencode-ai/sdk` 和 plugin 包版本也为 `1.18.31`。
- `serve --help` 支持 `--hostname`、`--port` 和 `--pure`。部署显式指定地址和端口，不依赖默认值。
- 本地 SDK 的 v2 类型含 `format: {type: "json_schema", schema, retryCount}` 和 Assistant Message 的 `structured` 字段；不能沿用原文未经核验的 `structured_output` 字段名。

本次只修订文档，未启动服务、修改现有 OpenCode 配置或验证模型调用。模型地址/ID/凭据、可用域名、对外访问网段、端口占用和系统服务管理权限在阶段 0 核实；不预设本机已经有模型服务。

---

## 2. V1 目标、边界与非目标

### 2.1 必须跑通的业务链路

1. 用户在页面选择示例场景手动触发；另可启用确定性定时触发。
2. 后端持久化 Trigger，创建 run 并获得回路独占权。
3. 从 `ObservationSource` 读取冻结的数据窗口和当前 PID，执行质量校验。
4. 通过真实 OpenCode 调用 Monitor Agent，校验结构化状态判断。
5. Guard 判断正常结束、进入调参或转人工复核。
6. 对初始 PID 执行一次基线评测，建立同条件的比较基准。
7. Control Agent 每轮只输出一个小步候选或 `ABSTAIN`。
8. 编排器校验、登记候选，通过 `CandidateEvaluator` 调用简单示例程序。
9. MetricsEvaluator 从结果曲线计算指标，决定接受、拒绝或停止。
10. 保存数据引用、配置快照、Agent 输出、工具摘要、指标和状态迁移。
11. 页面展示示例曲线、候选变化、建议 PID、执行轨迹和失败原因。
12. 服务重启后恢复未完成任务，已提交的评测结果不重复接受或执行。

### 2.2 本版不实施

- 真实 DCS/PLC/OPC UA/Modbus 连接及任何参数下发。
- 物理模拟器、实时仿真器、数字孪生或控制算法有效性验证。
- 自行部署/训练大模型、小型状态识别网络或强化学习策略。
- PostgreSQL、Redis、Kafka、时序数据库、对象存储、Kubernetes、Grafana 等独立基础设施。
- 多节点、高可用、多租户、复杂审批平台。
- 完整离线模型制品、隔离网络安装及生产断网验收。

Fake Agent 只用于自动化测试，最终 Demo 必须实际经过 OpenCode 的两个 Agent。模型不可用时明确显示不可用，不能静默切换 Fake 并宣称链路成功。

### 2.3 本版验收含义

`SUCCEEDED` 表示在示例条件下达到配置目标；`best_pid` 是本次 run 的推荐参数。页面固定显示“示例数据 / 未连接真实设备”。正常停止或转人工也是有效工作流结果，不要求模型每次找到同一候选或保证收敛。

---

## 3. 总体架构与技术选型

### 3.1 分层结构

```mermaid
flowchart TD
    U[浏览器] --> P[本机反向代理：网页 / API]
    P --> B[FastAPI：API + 调度 + 单 Worker 编排]
    B --> D[(SQLite：权威业务状态)]
    B --> O[OpenCode Server：127.0.0.1:4096]
    O --> M[Monitor Agent]
    O --> C[Control Agent]
    M --> T[只读 MCP Gateway]
    C --> T
    T --> R[后端内部只读接口]
    R --> D
    B --> S[ObservationSource：示例窗口与 PID]
    B --> E[CandidateEvaluator：ExampleEvaluator]
    E --> X[固定入口的简单示例程序]
    B --> G[Guard + MetricsEvaluator]
    X --> F[受控结果目录：JSONL]
    G --> F
```

业务 API、调度器和后台 Worker 在同一 Python 进程中启动，Worker 持续消费数据库任务。MCP 是 OpenCode 拉起的轻量 stdio 子进程，通过内部只读 API 获取后端已冻结的 run 数据；MCP 不直接写数据库。前端构建后由反向代理提供静态文件，无须独立前端 Node 常驻服务。

### 3.2 明确技术基线

| 层 | V1 选择 | 选择理由及边界 |
| --- | --- | --- |
| 后端 | Python 3.11+、FastAPI、Uvicorn，锁定实际兼容版本 | API、编排、数值处理、未来设备 Adapter 共用 Python；不额外引入 TS 中间服务 |
| 契约 | Pydantic v2 + 导出的 JSON Schema/OpenAPI | 后端模型作为契约源，前端类型生成；禁止双份手写 Schema 漂移 |
| OpenCode 客户端 | HTTPX 封装固定版本 HTTP API | 直接连接现有 OpenCode；SDK 类型和 `/doc` 用于校验字段 |
| MCP | 官方 Python SDK，stdio | 与 OpenCode 本地集成；无需额外监听对外端口 |
| 持久化 | SQLite + SQLAlchemy 2.x + Alembic | 迁移和 Repository 边界先建立；单进程、短事务，后续可替换 PostgreSQL |
| 曲线与指标 | 本地 JSONL、NumPy + 明确定义的纯函数 | 结果可重算；不引入时序服务 |
| 前端 | React + TypeScript + Vite + ECharts | 运行列表、曲线对比、时间线；采用 REST 轮询起步 |
| 部署 | systemd + Nginx | 本机常驻、进程重启、网页/API 同源访问 |
| 工程 | Python 虚拟环境和锁文件、npm 锁文件；pytest、Ruff、TypeScript 检查 | 固定依赖；不向现有 OpenCode 安装目录安装业务依赖 |

以上是实施选择，不代表已安装或联调。版本在阶段 0 根据本机 Python/Node 和依赖兼容性固定，不追随 `latest`。FastAPI 提供 OpenAPI 支持，Pydantic 可导出 JSON Schema；SQLite 的事务和驱动行为需在配置中明确。[FastAPI 文档](https://fastapi.tiangolo.com/)、[Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/)、[SQLAlchemy SQLite 文档](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)。

### 3.3 三组可替换边界

| 接口 | 当前实现 | 未来实现 | 禁止混入的职责 |
| --- | --- | --- | --- |
| `ObservationSource` | `ExampleObservationSource`，生成/读取固定窗口 | 内网历史库、采集网关的只读 Adapter | 不下发、不决定候选 |
| `CandidateEvaluator` | `ExampleEvaluator`，运行确定性示例程序 | 离线评测、历史回放、经验证的模拟器 Adapter | 不能执行真实控制写入 |
| `ControlCommandGateway` | 仅设计未来契约，无 V1 实现/路由 | 审批后的受控下发与回执查询 | 不作为 Agent Tool，不复用评测接口 |

可选模拟器是将来的评测实现，不是接入真实系统的必经技术产品。真实写入前必须另行完成适合现场的验证与审批；示例数据不能用于这种审批。

---

## 4. 工作流与状态机

### 4.1 正常路径

```mermaid
flowchart TD
    A[创建 run / 回路加锁] --> B[加载窗口 / 数据 Guard]
    B --> C[Monitor 结构化判断]
    C --> D{允许调参}
    D -->|正常| N[NO_TUNING_REQUIRED]
    D -->|不确定| M[MANUAL_REVIEW_REQUIRED]
    D -->|允许| E[初始 PID 基线评测]
    E --> F[Control 输出一个候选]
    F --> G[参数 Guard / 候选入库]
    G --> H[编排器调用候选评测器]
    H --> I[指标计算 / 接受或拒绝]
    I --> J{达到目标或停止条件}
    J -->|否| F
    J -->|是| K[提交终态 / 释放回路]
```

基线由相同评测器在同一 `evaluation_context_hash` 下生成，不能拿示例观测窗口的分数直接与另一条试算曲线比较。基线 job 的 `purpose=BASELINE`，不占候选迭代次数；候选 job 的 `purpose=CANDIDATE`。

### 4.2 状态枚举

```text
CREATED -> DATA_LOADING -> MONITOR_REQUESTED -> MONITOR_COMPLETED
  -> BASELINE_REQUESTED -> BASELINE_RUNNING -> BASELINE_COMPLETED
  -> CANDIDATE_REQUESTED -> CANDIDATE_VALIDATED
  -> TRIAL_REQUESTED -> TRIAL_RUNNING -> TRIAL_COMPLETED
  -> EVALUATING -> CANDIDATE_ACCEPTED | CANDIDATE_REJECTED
  -> CANDIDATE_REQUESTED（下一轮）或终态
```

终态只有：

```text
NO_TUNING_REQUIRED
MANUAL_REVIEW_REQUIRED
SUCCEEDED
STOPPED_NO_IMPROVEMENT
STOPPED_MAX_ITERATIONS
STOPPED_TIMEOUT
FAILED_FINAL
CANCELLED
```

`CANDIDATE_INVALID`、`RETRY_SCHEDULED`、`RECOVERY_STARTED` 等是审计事件；重试记录 `attempt_no` 和 `next_attempt_at`，不必增加悬空状态。数据质量不足直接以原因 `DATA_INVALID` 进入人工复核。Control 的 `ABSTAIN` 转人工复核。基线已达标时可直接 `SUCCEEDED`。停止判断优先级为取消、完整性/硬约束故障、总超时，再判断达标、无改善和轮数。

### 4.3 状态迁移与并发规则

- 每次事务同时更新状态、`state_version`、结果引用和审计事件；更新条件包含旧状态版本和当前 owner/fencing token。
- SQLite 对非终态 `control_loop_id` 建唯一约束（部分唯一索引）；内存队列仅用于唤醒，不能替代数据库约束。
- lease 由独立后台协程续期，不能等待模型返回才更新心跳；续期失败时停止派发新任务。心跳间隔、租约时长和接管宽限期配置化。
- 相同回路新触发记录 `TRIGGER_COALESCED` 并关联活动 run；最多保留一个待处理触发。
- 请求外部组件前提交 `*_REQUESTED` 和幂等键；等待网络/程序时不持有数据库写事务。
- 无效候选不创建评测 job。每轮最多一次格式修复和一次业务候选重提，重提沿用预分配 ID；候选通过 Guard 并绑定后不可改写。预算耗尽转人工复核。
- 接受只更新 `best_pid` 及其关联指标；拒绝保留原 best；两者都消耗一个有效候选轮次。
- 终态不能原地重开。用户重试创建带 `parent_run_id` 的新 run，并重新读取观测。
- 取消后撤销调用上下文并尽力终止在途任务；迟到结果只存审计，不能更新 best 或触发下一轮。

---

## 5. OpenCode 集成设计

### 5.1 本机运行方式

计划从项目专用运行目录启动已有二进制：

```bash
/data9/xjx/.opencode/bin/opencode serve --hostname 127.0.0.1 --port 4096
```

部署前确认端口未被占用；若已有无关服务则配置其他端口，不终止或复用未知实例。`OPENCODE_SERVER_PASSWORD` 通过受限环境文件注入；业务后端保存凭据，浏览器不接触 OpenCode。Server 的 Basic Auth 与 `/doc` 能力见 [OpenCode Server 文档](https://opencode.ai/docs/server/)。

项目独立保存 `opencode.jsonc`、Agent 和 Skill，运行数据写入项目服务目录。不能覆盖用户 `/data9/xjx/.opencode` 中已有配置或凭据；在阶段 0 核验该版本全局配置合并和插件加载行为，必要时使用独立 XDG 配置/数据目录。`--pure` 是否适合本项目需结合 MCP/provider 验证后决定。

### 5.2 角色与 Session

- `monitor` 和 `control` 均为由 Orchestrator 显式指定的自定义 primary agent，禁止 `task`。
- Monitor 解释窗口状态，只使用监测工具，不提 PID。
- Control 基于权威 best、约束和有限历史提出一个 PID 增量，只使用只读上下文工具；不调用评测器，不宣告候选被接受。
- 每 run、每角色一个 Session；run 结束后不再追加。每次 Prompt 仍携带数据库摘要，历史仅保留最近若干轮。
- Session 丢失时创建替代 Session，记录替换关系；业务恢复从数据库进行。
- Session 标题：`719agent/{environment}/{control_loop_id}/{run_id}/{role}`。

### 5.3 结构化输出与客户端

`OpenCodeClient` 暴露以下业务方法，不将版本 HTTP 路径散落到编排器：

```text
health()
list_agents()
create_session(title)
prompt_structured(session_id, agent, request_id, payload, schema, timeout)
get_messages(session_id)
get_session_status(session_id)
abort_session(session_id)
```

调用携带 allowlist 中的 provider/model、Agent 名称和 JSON Schema。Client 处理 Basic Auth、项目目录、连接/读取/总超时、消息关联、响应大小限制及错误码映射。根据本地 1.18.31 SDK v2 类型，HTTP 调用的请求/响应意图如下，阶段 0 仍需以实际 `/doc` 冒烟验证：

```json
{
  "agent": "monitor",
  "model": {"providerID": "project-llm", "modelID": "control-model"},
  "parts": [{"type": "text", "text": "<serialized MonitorRequest>"}],
  "format": {"type": "json_schema", "schema": {}, "retryCount": 0}
}
```

这里 `schema: {}` 只是位置示意，实现必须替换为完整业务 Schema。适配层从 Assistant Message 的 `info.structured` 提取业务结果，并核对消息角色、完成状态、错误、所属 Session 和请求关联。不得解析自由文本冒充结构化输出。设置 `retryCount=0` 后由编排器统一控制一次格式修复预算；若部署版本行为不同，先验证总重试上限，避免内外重复重试。

### 5.4 配置与权限基线

项目模型别名 `project-llm/control-model` 仅作示意；启动前按可用模型能力配置。Demo 可以使用明确配置的可访问 provider，内网阶段要求替换为内网 provider；Agent 自身的 Web 工具始终禁用。

以下为配置片段，MCP 命令和环境变量值在部署时生成：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "autoupdate": false,
  "share": "disabled",
  "permission": {"*": "deny"},
  "mcp": {
    "industrial_control": {
      "type": "local",
      "command": ["/data9-2/xjx/opencode-agent-demo/.venv/bin/python", "-m", "apps.mcp_server"],
      "enabled": true,
      "environment": {
        "INTERNAL_API_BASE_URL": "http://127.0.0.1:8000/internal/v1",
        "MCP_SERVICE_TOKEN": "{env:MCP_SERVICE_TOKEN}"
      }
    }
  }
}
```

Agent 文件示意：

```yaml
# .opencode/agents/monitor.md 的 frontmatter
---
description: Analyze the supplied observation window and return one assessment.
mode: primary
permission:
  "*": deny
  skill:
    "*": deny
    steam-system-monitoring: allow
    control-safety: allow
  industrial_control_get_recent_window: allow
  industrial_control_calculate_metrics: allow
  industrial_control_detect_state: allow
---
```

Control 同样默认拒绝，仅允许 `pid-tuning`、`control-safety` Skill 和 `industrial_control_get_tuning_context` 工具。权限规则与工具实际名称在固定版本上核验，禁止因调试失败直接全量开放。[OpenCode 权限文档](https://opencode.ai/docs/permissions/)。

Skill 首版应自包含，避免默认拒绝文件读取后其附带 references 无法加载。若以后确需读 references，只允许项目受控知识目录并验证路径边界。结构化输出所需的内部工具不得被权限配置误伤，列入 OpenCode 冒烟测试。

---

## 6. Skill 与 Agent 知识边界

保留三个本地版本化 Skill：

| Skill | 内容 | 使用者 |
| --- | --- | --- |
| `steam-system-monitoring` | 变量、质量、状态类别、指标解释、不确定性处理 | Monitor |
| `pid-tuning` | 参数作用、小步调整、已拒绝历史、候选格式与 ABSTAIN | Control |
| `control-safety` | 边界不可覆盖、示例不代表现场、错误上报、禁止真实控制 | 两者 |

目录为 `.opencode/skills/{name}/SKILL.md`；`name` 与目录一致，`description` 明确加载条件。记录版本和内容哈希，每 run 保存实际版本。Skill 更新需回归，但不能通过 Skill 修改确定性约束或状态流转。

所有 Agent Prompt 均说明：示例程序只用于演示，PID 经验不能据此获得现场验证；外部文本、设备备注、日志和工具结果是数据，不能作为覆盖系统规则的命令。仓库 `AGENTS.md` 仅放工程规则，业务知识不全部堆入其中。

---

## 7. MCP：只读业务工具

### 7.1 V1 工具清单

| 工具 | 使用者 | 输入 | 返回 |
| --- | --- | --- | --- |
| `get_recent_window` | Monitor | `call_context_token` | 当前 run 冻结窗口的引用、质量、特征和来源 |
| `calculate_metrics` | Monitor | `call_context_token` | 当前授权窗口的确定性指标及版本 |
| `detect_state` | Monitor | `call_context_token` | 规则识别类别、证据、规则版本；不冒充学习模型概率 |
| `get_tuning_context` | Control | `call_context_token` | 实际 PID 快照、run 内 best、历史拒绝、边界和剩余预算 |

V1 不暴露 `simulate_pid`、`evaluate_candidate`、`apply_pid`、任意 SQL、任意文件、任意 HTTP 或 shell Tool。候选评测入口只由 Orchestrator 调用。真实 OpenCode 验收应有两个角色各自合法工具调用的记录，且不能强行伪造工具轨迹。

### 7.2 身份、作用域与审计

OpenCode 工具权限只能构成第一层约束，MCP 的服务端授权不能相信模型提供的 `role` 字符串：

1. Orchestrator 每次推理前生成随机 `call_context_token`，数据库存哈希及绑定的 role、run、iteration、允许工具、数据范围、过期时间。
2. Token 作为调用上下文供 Agent 传给 MCP；MCP 自身通过环境中的服务凭据访问后端内部接口。服务凭据不出现在 Prompt。
3. 内部接口同时校验服务身份、调用 Token、run 未终止、作用域与工具 allowlist。Token 不能扩大权限或指定任意数据引用；Monitor Token 不能读取其他 run 或 Control 专属上下文。
4. 调用结束、取消、超时或重试时撤销旧 Token，替代请求签发新 Token；日志和页面不展示明文 Token。
5. 工具请求及返回摘要记录 `run_id`、`request_id`、工具名、耗时和错误码。MCP 不做状态迁移。

这避免把“模型声称自己是谁”当成认证。即使猜到 run ID，没有有效作用域 Token 也不能读取。stdio 是本地进程传输，日志写 stderr，stdout 仅输出协议消息。[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)。

### 7.3 大结果和错误

工具只返回摘要和后端生成的不透明 `series_ref`，不把完整曲线塞进 Prompt。引用解析必须检查所属 run 和调用作用域，不接受客户端文件路径或外部 URL。

统一错误结构为 `code`、`message`、`retryable`、`trace_id`；不返回堆栈和凭据。工具次数、响应大小及超时都有配置上限；OpenCode 会话完成不等于业务成功。

---

## 8. 集成契约与示例程序

### 8.1 公共契约

业务契约从 `packages/contracts` 的 Pydantic 模型生成 JSON Schema。请求禁止额外字段，数字必须有限，缺失值为显式 `null` 并带质量说明。未知主版本拒绝，新增可选字段在版本兼容策略下处理。

公共字段包括：

- `schema_version`、`trace_id`、`control_loop_id`。
- 服务分配的 `run_id`、`iteration_id`、`candidate_id`；基线 job 的后两者为空。
- `source_kind: EXAMPLE | SIMULATION | REAL`、`adapter_id`、`adapter_version`。
- 绝对时间统一为 UTC ISO 8601；曲线另带从固定起点计算的 `elapsed_seconds`。
- `units`、`pid_form`、`sample_period_seconds`、质量标记；Demo 使用 `demo_unit` 与 `DEMO_PARALLEL_PID`，不能用于真实接入。
- 数据引用附 `sha256`、样本数、时间范围；参数和配置带独立版本号。

### 8.2 观测源接口

以下是领域接口，Demo 用进程内 Python 实现，不必新增 HTTP 服务；以后由 Adapter 封装厂商协议：

```python
class ObservationSource(Protocol):
    async def capabilities(self) -> SourceCapabilities: ...
    async def health(self) -> ComponentHealth: ...
    async def read_window(self, query: ObservationQuery) -> ObservationWindow: ...
    async def read_pid(self, control_loop_id: str) -> PidSnapshot: ...
```

`ObservationQuery` 包含回路、窗口起止、字段 allowlist 和请求 ID；Adapter 配置来自服务端，不接受用户指定地址/协议/凭据。

`ObservationWindow` 包含 `snapshot_id`、`series_ref`、哈希、时间范围、采样率、质量摘要、工况版本、字段单位和来源。`PidSnapshot` 包含 `pid`、`pid_version`、`observed_at`、`pid_form`、参数量纲和来源。窗口与 PID 的时间/工况版本必须匹配，超出容差返回 `SNAPSHOT_MISMATCH`；不可仅因为两个请求都成功就视为一致。

每个 run 冻结数据快照，恢复时重用。观测 source 与候选评测器分别配置；未来“真实观测 + 仿真候选”允许存在，但不能用真实观测分数代替仿真基线。

### 8.3 候选评测接口

```python
class CandidateEvaluator(Protocol):
    async def capabilities(self) -> EvaluatorCapabilities: ...
    async def health(self) -> ComponentHealth: ...
    async def submit(self, request: TrialRequest) -> TrialReceipt: ...
    async def get(self, idempotency_key: str) -> TrialStatus: ...
    async def cancel(self, idempotency_key: str) -> CancelReceipt: ...
```

能力声明包含协议/Adapter 版本、支持的来源、PID 形式、异步查询、幂等和取消能力。是否支持必须显式返回，不通过异常猜测。V1 只注册 Example 实现，`can_apply_control=false`；取消不支持时返回 `UNSUPPORTED_CAPABILITY`，后端仍可停止业务 run 并隔离迟到结果。

`TrialRequest` 示例：

```json
{
  "schema_version": "1.0",
  "run_id": "run_001",
  "iteration_id": "iter_001",
  "candidate_id": "cand_001",
  "control_loop_id": "feedwater-pump-demo",
  "purpose": "CANDIDATE",
  "idempotency_key": "run_001:iter_001:trial",
  "candidate_pid": {"kp": 1.05, "ki": 0.2, "kd": 0.05},
  "evaluation_context_ref": "context_001",
  "evaluation_context_hash": "<sha256>",
  "request_hash": "<sha256>",
  "timeout_seconds": 10
}
```

`evaluation_context_hash` 覆盖冻结的场景、初始条件、目标、单位、采样窗口、seed、Adapter/程序版本和指标配置，**不包含候选 PID**，用于基线与候选的可比性检查。`request_hash` 覆盖实际评测请求的规范化内容（不含自身），用于同键不同内容的冲突检测。Hash 序列化规则固定，不能直接 hash 无序 JSON 文本。

返回包括：

```json
{
  "schema_version": "1.0",
  "trial_id": "trial_001",
  "run_id": "run_001",
  "iteration_id": "iter_001",
  "candidate_id": "cand_001",
  "purpose": "CANDIDATE",
  "idempotency_key": "run_001:iter_001:trial",
  "status": "COMPLETED",
  "source_kind": "EXAMPLE",
  "adapter_id": "example-evaluator",
  "adapter_version": "example-v1",
  "evaluation_context_hash": "<sha256>",
  "request_hash": "<sha256>",
  "series_ref": "series_001",
  "series_sha256": "<sha256>",
  "sample_count": 121,
  "units": {"pressure": "demo_unit", "time": "s"},
  "error": null
}
```

以上为核心字段示意；完整契约还包括 `trace_id`、提交/开始/完成时间，结果曲线必须携带 `elapsed_seconds` 与质量字段。Job 状态为 `QUEUED | RUNNING | COMPLETED | FAILED | CANCELLED | UNKNOWN`。`get` 返回 `NOT_FOUND` 时必须表示实现端权威确认未登记；网络超时返回 `UNKNOWN`/可重试错误，不能伪装成不存在。只有 `COMPLETED` 且身份、内容、哈希、样本和上下文全部匹配才允许评估。

### 8.4 幂等与持久化边界

- Orchestrator 先提交 job 记录与请求哈希，再派发；ExampleEvaluator 的持久化登记复用后端 Repository，示例子进程不直接访问数据库。
- 同一幂等键与相同请求返回同一 `trial_id`；同键不同请求返回 `IDEMPOTENCY_CONFLICT`。
- 结果写入临时文件，完成校验后原子改名，再提交数据库结果引用；恢复时可重新登记已完成且哈希一致的文件。
- 运行中超时先查询，不能直接重新提交。确认原示例进程已退出、没有有效结果后，可以在同一 job 下重算纯确定性示例，并记录 attempt。
- 本设计保证一个有效结果及一次接受提交，不宣称跨进程崩溃时程序物理执行绝对一次。未来外部 Adapter 必须提供持久幂等/查询保证，否则未知结果转人工，不能自动重试副作用。

### 8.5 简单示例程序的具体范围

`examples/pid_response.py` 只做 JSON 输入、确定性数值生成、JSONL 输出，无真实设备、无微分方程求解器、无实时闭环。示例可按以下方式实现：

1. 为 `normal`、`steady_high`、`oscillation` 等场景保存固定残差曲线 `r_s(t)` 和演示 PID 参考值。
2. 将候选参数与演示参考值的归一化距离转成非负缩放系数，例如 `q = 1 + sum(((pid - demo_reference) / scale) ** 2)`。
3. 生成 `pressure(t) = target + q * r_s(t)`；必要的控制输出为另一个有版本的固定示例函数。
4. 初始观测、基线和候选使用同一场景版本；随机扰动如启用必须使用固定 seed。程序不能返回“接受/拒绝”，指标由独立代码计算。
5. 专门场景覆盖缺失值、超时、失败和无改善；故障注入仅测试配置可启用，普通触发 API 不接受任意故障开关。

这只是可重复的示例映射，不声称 PID 与压力存在上述物理关系。演示参考值只保存在示例配置中，不用于推断真实参数。前端、报告及结果元数据均标记来源。

Adapter 使用固定入口和参数列表启动子进程（不经 shell），stdin 输入请求；输出目录由服务端分配。限制执行时间、stdout/stderr 大小、曲线样本数；进程超时终止整个进程组并等待退出。CLI 路径、命令和输出路径都不能由 Agent 或浏览器指定。

### 8.6 Monitor/Control 输入输出

`MonitorRequest` 必须包含请求/业务 ID、调用 Token、目标、单位、观测摘要、当前 PID 快照、规则结果、阈值和来源。输出示例：

```json
{
  "schema_version": "1.0",
  "assessment": "STEADY_HIGH",
  "confidence": 0.9,
  "needs_tuning": true,
  "manual_review_required": false,
  "evidence": [{"feature": "steady_state_error", "value": 1.2}],
  "reason": "示例窗口末段持续高于目标。",
  "warnings": []
}
```

状态枚举为 `NORMAL_STABLE`、`STEADY_HIGH`、`STEADY_LOW`、`EXCESSIVE_OVERSHOOT`、`SUSTAINED_OSCILLATION`、`SLOW_RESPONSE`、`OPERATING_CONDITION_CHANGED`、`DATA_ANOMALY`、`UNCERTAIN`。LLM 自报 confidence 不是校准概率，不能覆盖数据 Guard 或规则冲突。

`ControlRequest` 包含预分配 ID、调用 Token、实际 PID 快照、当前 best 与其分数/指标、固定评测上下文、最近历史、绝对边界、步幅和剩余预算。输出：

```json
{
  "schema_version": "1.0",
  "decision": "PROPOSE",
  "delta": {"kp": 0.05, "ki": 0.0, "kd": 0.0},
  "candidate_pid": {"kp": 1.05, "ki": 0.2, "kd": 0.05},
  "reason": "在给定约束内尝试一次小步调整。",
  "expected_effect": "比较示例指标是否改善。",
  "risk_notes": ["仅用于示例评测"]
}
```

若 `ABSTAIN`，`delta` 和 `candidate_pid` 必须为 `null` 且有原因。Control 输出**不包含评测结果/ID**，这些只能由编排器写入。校验 `candidate_pid = best_pid + delta`（配置浮点容差），禁止使用设备实际 PID 代替当前 best 计算后续增量。

### 8.7 未来真实写入契约（仅预留）

未来 `ControlCommandGateway` 应独立提供 `get_capabilities`、`submit_approved_command`、`get_command_status`。命令至少包含审批记录/令牌、回路、期望 PID 版本、期望工况版本、有效期、候选参数、幂等键和操作者；返回命令 ID、状态、网关回执、写后读取值与设备版本。

网关需拒绝版本过期、审批失效、工况变化和越界参数，区分“已接收”“已下发”“已读取确认”。网络未知不等于成功，回退是独立审批命令。Demo 不实现这些方法，不暴露空壳写入 API；未来不能仅通过 `mode=real` 打开写入。

---

## 9. Guard 与确定性指标

### 9.1 数据与参数 Guard

数据检查样本数、时间单调性、采样间隔、缺失率、冻结/突跳、单位、PID/工况快照一致性及数据新鲜度。固定示例窗口的新鲜度相对场景虚拟时钟判断，不能把旧 fixture 当成现场实时数据。

参数边界同时满足：

\[
\theta' = \theta_{best} + \Delta\theta,\qquad
\theta_{min} \le \theta' \le \theta_{max},\qquad
|\Delta\theta| \le \Delta\theta_{max}
\]

所有分量为有限数；约束取 run 的配置快照。禁止模型更改目标值、场景、seed、参数边界、权重或阈值。重复已评测的参数不创建新 job，记拒绝/停止原因，不能无限重复。

### 9.2 MetricsEvaluator

`CandidateEvaluator` 负责产生评测曲线；`MetricsEvaluator` 负责确定性指标与裁决，二者不能混为一个会自行宣布成功的工具。

V1 必做：末段平均偏差的绝对值（稳态误差）、IAE、超调比例、末段标准差、稳定时间。控制输出总变差和饱和比例仅在存在对应字段时计算；缺失且配置为必需项则拒绝结果，不能默认填零。上升时间仅对定义完整的阶跃场景有意义，否则返回 `null` 和原因。

\[
e(t)=p(t)-p^*,\qquad IAE=\int_0^T|e(t)|dt
\]

\[
O=\max(0,(\max_t p(t)-p^*)/\max(|p^*|,\epsilon))
\]

该超调定义用于当前正目标示例；真实工况的阶跃方向、基准和量纲需另行确认。指标版本固定末段比例、积分规则、稳定带、保持时长和归一化尺度。稳定时间未达到时返回 `null`，评分使用配置的窗口上限惩罚，同时 `target_reached=false`；不能将“未稳定”编码为零秒。

\[
J = w_e\hat E + w_o\hat O + w_s\hat S + w_t\hat T_s
\]

V1 综合分数使用上述四项；所有归一化尺度固定在配置中，不能随本轮样本自适应改变。IAE 先展示与诊断，不必加入评分。结果哈希、来源、上下文、指标版本不匹配时不进入比较。

### 9.3 接受与停止

```text
accept = hard_constraints_passed
         AND result_integrity_passed
         AND candidate_score <= best_score - min_improvement
```

达标要求所有配置的目标指标满足阈值；硬约束失败不能因为总分改善而接受。记录无改善轮数、有效轮数、总耗时和模型调用预算，达到上限停止。Demo 阈值可由开发者明确标记 `DEMO_ONLY` 后配置；真实阈值必须由控制专业人员确定。

---

## 10. 持久化与结果存储

### 10.1 SQLite 使用边界

数据库放在本机受控的本地磁盘路径；部署前确认挂载支持所需锁/WAL 语义，不能默认 `/data9-2` 是本地盘。启用外键、WAL、busy timeout，事务短小；V1 Uvicorn 固定单 worker，不用开发热重载运行常驻任务。数据量/多实例需求出现后再迁移 PostgreSQL，届时补方言、锁和恢复测试，不能认为改连接串即可完成迁移。

### 10.2 核心表

| 表 | 关键字段与约束 |
| --- | --- |
| `trigger_event` | event_id、deduplication_key 唯一、类型、回路、场景、时间、关联 run |
| `control_run` | 状态/state_version、parent_run_id、回路、实际 PID 快照、best PID/指标/分数、基线 job、数据引用、配置/模型/Skill 版本、来源、owner/lease/fencing、停止原因 |
| `control_iteration` | run_id + iteration_no 唯一、唯一 candidate_id、基线 best、delta/候选 PID、trial_id、指标、接受标记、原因 |
| `trial_job` | 唯一 idempotency_key、request_hash、purpose、run/iteration/candidate、上下文哈希、Adapter/程序版本、状态、attempt、进程信息、结果引用/哈希、错误 |
| `state_assessment` | run、观测摘要、规则输出、Monitor 结果、消息 ID |
| `agent_request` | request_id、run/角色/轮次、Session/Message ID、请求哈希、状态、attempt、原始结构化输出引用 |
| `call_context` | Token 哈希、请求/角色/数据作用域、允许工具、过期/撤销时间 |
| `audit_event` | 单调事件 ID、run/iteration、actor、old/new state、payload 摘要、trace_id、时间 |
| `series_artifact` | series_ref、受控路径、哈希、样本/时间范围、来源、单位、所属 run/job |
| `api_idempotency` | 操作者/路由/幂等键唯一、请求哈希、响应状态/引用，按保留策略清理 |

### 10.3 文件与数据库一致性

曲线 JSONL 不重复存入数据库，也不通过 Agent 传输。文件先完成后登记，数据库记录缺失文件或哈希错误时阻止评估；未引用的临时文件按保留期清理，活动 run 文件不得提前删除。

数据库状态与审计同事务提交；API/Worker/MCP 无法各自维护独立 best。初始实际 PID、run 内 best、最终推荐是三个不同字段。推荐不写回 ExampleObservationSource，下一次 run 仍从其明确的场景初始条件开始。

---

## 11. Orchestrator 实现方案

### 11.1 单进程后台 Worker

FastAPI 接收触发后在一个事务中落库，返回 `202` 与查询地址；Worker 从数据库扫描到期任务并执行。内存通知仅优化延迟。不能把 HTTP 请求生命周期内的临时 BackgroundTask 当作持久任务系统。

异步 HTTP 调用不阻塞 API；示例程序通过受控子进程运行。Worker 每次只处理有限步骤，持久化后让出执行权；可查询、可取消、可停止接收新触发。

### 11.2 处理逻辑示意

```python
async def advance_run(run_id):
    run = repository.claim_and_reload(run_id)
    if run.is_terminal:
        return
    if run.cancel_requested:
        return await cancel_and_finalize(run)
    if run.deadline_exceeded:
        return await stop_and_finalize(run, "STOPPED_TIMEOUT")

    # 按持久化状态分派一个处理器；每个处理器完成一次可恢复步骤。
    handler = transition_handlers[run.status]
    await handler(run)
```

Handlers 依次实现：观测质量校验、Monitor、基线提交/查询、Control、候选校验登记、评测提交/查询、指标裁决。每一步重新加载权威记录，不跨恢复点依赖内存变量。

### 11.3 版本与配置冻结

每个 run 保存实际 provider/model 标识、OpenCode 版本、Prompt/Skill 哈希、评测器/示例程序/场景版本、指标版本和完整非敏感配置快照。配置更新只影响新 run。恢复时所需旧版本不可用则停止并说明，不能静默用新程序改写旧 job。

---

## 12. 触发机制

V1 同时提供页面手动触发和可关闭的定时触发。定时器运行在 Orchestrator 的确定性调度模块中，不依赖 Agent 会话；只有一个调度实例，使用固定时间槽生成幂等键。首版不再额外部署 systemd timer，避免两套调度重复触发。

```json
{
  "schema_version": "1.0",
  "event_id": "evt_001",
  "event_type": "MANUAL",
  "control_loop_id": "feedwater-pump-demo",
  "scenario_id": "steady_high",
  "occurred_at": "2026-09-22T09:00:00Z",
  "deduplication_key": "manual-request-001"
}
```

`event_type` 当前支持 `MANUAL | TIMER`，未来扩展 `STATE_ANOMALY`；实际发生时间和操作者由后端记录。`scenario_id` 只允许注册场景，真实观测 Adapter 不接受该字段。重启后不补跑所有错过的时间槽，最多合并最近一个待处理触发。

未来小型状态识别服务只负责产生同契约 TriggerEvent，不能绕过编排器。`detect_state` 当前采用透明规则，并保留替换点，无训练任务。

---

## 13. Dashboard 与可观测性

### 13.1 页面范围

1. **总览与触发**：服务就绪状态、示例场景、手动开始/取消、定时开关、活动 run 和历史列表。
2. **运行详情**：Monitor 判断、来源/单位、观测实际 PID、run 内 best/最终推荐、每轮指标及停止原因。
3. **曲线与轨迹**：目标线、观测/基线/候选曲线、接受/拒绝标记、状态时间线、Agent 理由、工具摘要和错误。

比较曲线使用 `elapsed_seconds` 对齐；真实观测和示例/仿真结果不同来源时分开展示并标记。页面不能把接受候选渲染成“已下发设备”。不提供真实应用按钮，人工复核仅用于记录意见/发起新检查。

API 返回曲线时可按像素/样本上限降采样，指标必须使用完整数据；不能从图表降采样点重算评分。大结果通过认证后的 series API 获取。

### 13.2 刷新策略

V1 使用 REST 分页和 2 秒轮询（可配置），足以展示长耗时 Agent 工作；事件接口支持 `after_event_id` 递增拉取。后续可加业务 SSE，不直接将 OpenCode 事件流公开给浏览器。流中断不得影响业务正确性。

OpenCode Web 仅用于受控运维，业务页面无需嵌入它。前端不持有模型凭据、MCP Token 或 OpenCode 密码。

### 13.3 日志和基础指标

结构化日志含 `timestamp`、`level`、`service`、`trace_id`、`run_id`、`iteration_id`、`trial_id`、Session/Message ID、事件、耗时和错误码。记录用于解释的输出，不要求或展示模型隐藏思维链。

V1 通过日志和内部统计接口观察 run 数、失败率、Agent/评测耗时、重试、恢复和质量失败；不要求独立 Prometheus/Grafana。敏感头、Token 和环境变量值脱敏，轮转日志并配置磁盘保留上限。

---

## 14. 本机服务与安全边界

### 14.1 对外访问的定义

本机向允许的客户端提供业务网页与 `/api/v1`，不等于向外开放 OpenCode、MCP、数据库或将公网连接到控制网。默认拓扑为：

```text
浏览器 -> 本机 Nginx:443 -> 静态 Dashboard
                       -> 127.0.0.1:8000/api/v1
后端  -> 127.0.0.1:4096 OpenCode -> 已配置模型 endpoint
OpenCode -> stdio MCP -> 127.0.0.1:8000/internal/v1
后端  -> 固定示例程序 + SQLite + 结果目录
```

443、8000、4096 均为计划值，实施前核实占用。Nginx 只转发明确的公共路径；拒绝 `/internal/`，不代理 OpenCode `/doc`、Session、配置或文件 API。后端内部接口独立服务身份认证，不能仅依赖来源 IP。

### 14.2 Demo 认证

V1 采用 Nginx TLS + Basic Auth 的简单账户入口；凭据使用密码文件哈希，不写进前端。Nginx 覆盖外部传入的操作者头并写入自身认证的用户标识，通过受限代理凭据标识受信任请求，后端验签/校验该凭据。内部 MCP 服务凭据与代理凭据分开。

后端拒绝未经可信代理认证的公共读写请求；仅回环可用的 liveness 不返回敏感数据。Basic Auth 浏览器请求仍需防 CSRF：写接口校验允许的 Origin，并要求从同源接口获得的 CSRF Token；CORS 默认关闭。设置请求体、触发频率和活动 run 总量限制。

域名、证书、外部访问网段按实际部署填写；证书可来自已有网关/内部 CA，不将公网自动签发服务作为依赖。多角色/企业 SSO 后续再接。

### 14.3 运行权限与未来边界

- 业务服务使用项目专用配置/状态目录和最小所需权限；未来内网部署改为专用低权限账户。
- OpenCode 禁用 bash、编辑、外网工具、task；模型网络出口仅指向所选 provider。
- 后端 Adapter 仅访问 allowlist 端点；请求不能选择任意地址、协议或可执行文件。
- V1 没有真实控制驱动和凭据。后续生产依次完成只读验证、专业验证、审批网关、受控下发及反馈监测。
- 底层保护与人工紧急操作不依赖 Agent 响应。示例、仿真通过不等于获得真实写入授权。

---

## 15. 故障、恢复、取消与备份

### 15.1 统一错误码

```text
INVALID_ARGUMENT / DATA_INVALID / DATA_NOT_READY / SNAPSHOT_MISMATCH
RUN_STATE_CONFLICT / CONSTRAINT_VIOLATION / CANDIDATE_MISMATCH
IDEMPOTENCY_CONFLICT / UNSUPPORTED_CAPABILITY / UNAUTHORIZED_SCOPE
OPENCODE_UNAVAILABLE / AGENT_TIMEOUT / AGENT_OUTPUT_INVALID
EVALUATOR_UNAVAILABLE / TRIAL_TIMEOUT / TRIAL_FAILED / RESULT_UNKNOWN
RESULT_INTEGRITY_ERROR / CONFIG_VERSION_UNAVAILABLE / INTERNAL_ERROR
```

Schema 错误最多一次格式修复；越界不是网络错误，不能原样重试。基础设施错误按 `retryable` 有限退避，每次尝试计入 run 总预算。只读请求可重试；有 job 的调用先查持久记录。

### 15.2 恢复扫描

1. 启动 API，允许历史查询；Worker 检查版本、数据库与文件完整性。
2. 扫描非终态 run；仅在 owner 确认失活且 lease 过期后接管，递增 fencing token，旧 owner 结果不能提交。
3. `MONITOR_REQUESTED` / `CANDIDATE_REQUESTED` 根据持久 request/message 关联查询 Session。已有完整合法响应则复用；调用仍活跃则等待/中止。不能仅看 Session 最后一条消息猜测对应请求。
4. `BASELINE_*` / `TRIAL_*` 查幂等键；完成则校验文件并继续，运行则等待，未知则查询到预算耗尽并停止/转人工。
5. `TRIAL_COMPLETED` / `EVALUATING` 可重算确定性指标；按状态版本幂等提交接受决定。
6. 每次接管和恢复记录原因、旧/新 owner、前后状态。

OpenCode 的请求也可能在网络超时后继续运行。替代调用前先确认旧调用已停止并撤销其 MCP Token；无法确认时停止 run，迟到输出只入审计。不能把一次格式重试变成两条同时活动的 Control 推理。

### 15.3 取消与超时

取消先落库，撤销 Token，调用 OpenCode abort 与评测器 cancel；不支持取消的任务允许后台结束但不再改变业务终态。提交接受事务时必须再次检查取消标记、期限和 fencing token，避免取消与完成并发导致参数被接受。

### 15.4 备份与回退

V1 交付停止接收新触发、等待/终止活动任务、备份 SQLite 与已引用结果文件的脚本，或使用 SQLite 一致性备份接口；不能在线只复制 `.db` 而漏掉 WAL。至少演练一次恢复查询与继续运行。

Demo 的“回退”仅恢复 run 内上一组已接受建议及其指标，并保留审计；候选拒绝通常直接保留 best 即可。不存在设备回退动作。版本回退需同时检查数据库迁移兼容性。

---

## 16. 配置管理

### 16.1 Demo 配置示意

以下数值全部为流程演示默认值，不是生产参数；实际配置模型还须补齐数据质量、特征规则与日志保留字段，并通过 Schema 校验。

```yaml
environment: demo
demo_only: true

service:
  host: 127.0.0.1
  port: 8000
  workers: 1
  state_dir: /data9-2/xjx/opencode-agent-demo/var  # 部署前确认本地磁盘语义

control_loop:
  id: feedwater-pump-demo
  target_pressure: 30.0
  pressure_unit: demo_unit
  pid_form: DEMO_PARALLEL_PID
  initial_pid: {kp: 1.0, ki: 0.2, kd: 0.05}

adapters:
  observation: {type: example, version: example-v1}
  evaluator: {type: example, version: example-v1}
  control_write_enabled: false  # V1 Schema 只允许 false，不是功能开关

example:
  default_scenario: steady_high
  scenario_version: scenarios-v1
  seed: 42
  duration_seconds: 120
  sample_period_seconds: 1
  max_samples: 1000
  request_timeout_seconds: 10

pid_constraints:
  kp: {min: 0.0, max: 3.0, max_delta: 0.1}
  ki: {min: 0.0, max: 1.0, max_delta: 0.05}
  kd: {min: 0.0, max: 0.5, max_delta: 0.02}
  equality_tolerance: 0.000001

optimization:
  max_iterations: 5
  max_no_improvement_rounds: 3
  run_timeout_seconds: 900
  min_improvement: 0.001
  format_retries: 1
  candidate_retries: 1

evaluation:
  metrics_version: metrics-v1
  steady_tail_fraction: 0.2
  settling_band_absolute: 0.3
  settling_hold_seconds: 10
  missing_settling_time_penalty_seconds: 120
  weights: {steady_state_error: 0.4, overshoot_ratio: 0.2, pressure_std: 0.2, settling_time_s: 0.2}
  scales: {steady_state_error: 1.0, overshoot_ratio: 0.1, pressure_std: 1.0, settling_time_s: 120.0}
  targets: {steady_state_error: 0.2, overshoot_ratio: 0.05, pressure_std: 0.2, settling_time_s: 90.0}

opencode:
  binary: /data9/xjx/.opencode/bin/opencode
  expected_version: 1.18.31
  base_url: http://127.0.0.1:4096
  monitor_agent: monitor
  control_agent: control
  request_timeout_seconds: 120
  # provider/model 从项目运行环境读取，启动前必填并验证

scheduler:
  enabled: false
  interval_seconds: 300
```

### 16.2 配置规则

- Secret 只从权限受控环境文件注入，API/日志不输出完整环境；配置快照剔除 Secret。
- provider/model、目录、边界和阈值缺失时启动报错或 readiness 降级，不猜测凭据/生产值。
- Demo 配置只支持 example Adapter 和关闭写入；未实现能力返回明确错误。
- 所有值附配置版本/哈希，新版本不改变活动 run。
- 真实接入需独立配置 Schema，明确 PID 形式/单位转换与实际限值，不直接复制本 YAML。

---

## 17. 仓库结构计划

```text
opencode-agent-demo/
├── AGENTS.md
├── README.md
├── opencode_multi_agent_control_architecture.md
├── opencode.jsonc
├── .opencode/
│   ├── agents/{monitor,control}.md
│   └── skills/{steam-system-monitoring,pid-tuning,control-safety}/SKILL.md
├── pyproject.toml
├── apps/
│   ├── orchestrator/          # FastAPI、调度、后台 Worker
│   ├── mcp_server/            # stdio，只读内部 API 客户端
│   └── dashboard/             # React/TS/Vite/ECharts
├── packages/
│   ├── contracts/            # Pydantic 模型、Schema 导出
│   ├── domain/               # 状态机、Guard、停止规则
│   ├── metrics/              # 指标与评分
│   ├── adapters/             # 接口与 Example 实现
│   ├── clients/              # OpenCodeClient
│   └── persistence/          # Repository、SQLAlchemy
├── examples/
│   ├── pid_response.py       # 固定入口的示例程序
│   └── scenarios/            # 场景、曲线、版本化 manifest
├── configs/{demo,test}.yaml
├── migrations/
├── deploy/{systemd,nginx}/
├── scripts/                  # doctor、启动、备份、恢复、导出契约
├── tests/{unit,contract,integration,recovery,e2e}/
├── docs/{api,adr,runbooks}/
└── var/                      # DB、曲线、日志、运行数据，不进 Git
```

实施阶段创建锁文件与 `.gitignore`。当前仅文档修订，不提前生成空目录、运行文件或伪实现。建议 ADR 记录：OpenCode 运行时、外部编排、DB 权威状态、示例评测接口、Agent 只提候选、真实写入独立网关。

---

## 18. 业务 API

### 18.1 V1 路由

```text
GET    /api/v1/scenarios
POST   /api/v1/triggers
GET    /api/v1/runs?cursor=...&limit=...
GET    /api/v1/runs/{run_id}
GET    /api/v1/runs/{run_id}/iterations
GET    /api/v1/runs/{run_id}/events?after_event_id=...
GET    /api/v1/series/{series_ref}?max_points=...
POST   /api/v1/runs/{run_id}/cancel
POST   /api/v1/runs/{run_id}/retry
POST   /api/v1/runs/{run_id}/manual-review
GET    /api/v1/control-loops/{id}/status
GET    /api/v1/scheduler
PUT    /api/v1/scheduler
GET    /api/v1/health
GET    /api/v1/csrf-token
POST   /api/v1/chat/sessions
GET    /api/v1/chat/sessions
GET    /api/v1/chat/sessions/{session_id}/messages
POST   /api/v1/chat/sessions/{session_id}/messages
```

对话写入使用现有 Origin、CSRF 与 Idempotency-Key；会话按身份隔离，消息包含公开 role、内容、状态及可选 run ID。普通问答会向 reception 提供最近对话及最近一次 run 的公开快照；快照可能过时，实时状态仍以 run API 为准。检查意图创建 run 后由原有轮询与事件 API 呈现。

`manual-review` 仅记录 `ACKNOWLEDGE | REQUEST_RECHECK` 及意见，`REQUEST_RECHECK` 创建新 Trigger；不能原地恢复终态或批准真实下发。定时配置变更审计操作者，只影响新触发。series 接口做所属 run 授权和引用解析，不允许传文件路径。

### 18.2 HTTP 语义

- 写请求必须带 `Idempotency-Key`；同身份/路由/键同内容返回原响应，不同内容 `409`。
- 触发/重试异步返回 `202` 与 run/trigger ID；重复触发返回其原 run 或合并的活动 run 标识。
- Schema/参数错误 `422`，认证失败 `401`，无权限 `403`，未知资源 `404`，状态冲突 `409`，超额 `429`，依赖未就绪 `503`。
- 错误统一包含业务 code、message、retryable、trace_id；客户端不能任意设置状态、best PID 或程序路径。
- OpenAPI 与 JSON Schema 存档，前端由生成类型调用，不手写另一套契约。

### 18.3 健康检查

liveness 仅说明进程存活；readiness 检查数据库、磁盘可写、场景/配置、OpenCode 版本与 Agent、所选模型及 Adapter。模型就绪不能仅凭 OpenCode `/global/health` 成功推断；启动冒烟和低频探测结合最近调用结果判断，避免每次网页轮询都调用模型。

依赖不可用时 `degraded`：保留历史查询和取消，拒绝新 run。健康详情显示版本和故障原因，不能输出 endpoint 凭据。

---

## 19. 本机部署方案

### 19.1 服务拆分与启动顺序

V1 仅需以下常驻服务：

```text
719agent-opencode.service       # 已安装二进制；MCP 由其拉起
719agent-backend.service        # API + scheduler + 单 Worker
nginx                          # 复用/配置静态网页和 API 路由
```

没有 simulator service、独立 dashboard service、Redis 或本机模型服务的强制依赖。已有模型可以在本机或可访问 endpoint，由 provider 配置决定。

先创建虚拟环境/前端构建产物/受控数据目录并执行迁移，再启动后端查询与内部 API、OpenCode 和 Nginx；所有依赖 ready 后 Worker 才领取新任务。避免“后端等待 MCP、MCP 又等待后端”造成启动循环；服务启动顺序与业务就绪分开判断。

### 19.2 systemd 样例

以下是以当前用户安装的 user unit 示例，路径和状态目录在阶段 0 确认后生成，不是本次已部署结果：

```ini
[Unit]
Description=719agent OpenCode Runtime

[Service]
Type=simple
WorkingDirectory=/data9-2/xjx/opencode-agent-demo
EnvironmentFile=/data9-2/xjx/opencode-agent-demo/deploy/runtime/opencode.env
ExecStart=/data9/xjx/.opencode/bin/opencode serve --hostname 127.0.0.1 --port 4096
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=default.target
```

环境文件不进 Git、权限 `0600`，包括 Server 密码、明确配置的 provider 和隔离运行目录变量。后端 unit 使用项目 `.venv/bin/python -m uvicorn apps.orchestrator.main:app --host 127.0.0.1 --port 8000 --workers 1`；不使用 `--reload`。

若本机无可持续运行的 user systemd manager，则改为系统 unit 和适当运行账户；user unit 需确认注销后持续运行/开机启动条件。不要直接照搬 `ProtectHome=true` 导致现有安装路径或凭据不可读，也不要假设 `/usr/local/bin/opencode` 存在。

### 19.3 对外发布与运维交付

- 前端 `npm run build` 生成静态产物，由 Nginx 托管；开发服务器和 `vite preview` 不作为常驻生产服务。[Vite 部署文档](https://vite.dev/guide/static-deploy.html)。
- `/api/v1` 转发到本机后端，SPA fallback 不得吞掉 API 错误；`/internal/` 明确拒绝。
- 配置 TLS、账户、CSRF、允许来源及网段；仅所需网页入口可被外部访问。
- 提供 doctor 命令检查二进制版本、端口、模型、目录、DB、Agent、MCP 和示例程序。
- 交付启动/停止/重启、日志查询、配置更新、备份恢复与故障排查步骤。

---

## 20. 后续内网接入与离线部署（不属于 V1 验收）

### 20.1 只读接入先行

与现场人员确认协议、数据点映射、压力单位、PID 形式（并联/串联、积分时间或增益等）、采样率、时钟、质量码、参数版本与工况定义。之后实现 `ObservationSource` Adapter，保留原始字段并做有版本的转换，使用真实只读数据验证新鲜度、质量与一致性。

协议选择由现场接口决定；先预留 OPC UA、Modbus、HTTP/历史库 Adapter 的位置，不同时开发所有驱动。只读阶段不启用候选实际下发。

### 20.2 评测与真实控制分开推进

视可用资源接入离线回放、经验证的模拟器或其他获认可的评测手段，沿用 `CandidateEvaluator` 契约。确认其适用工况、局限性和持久幂等能力，建立真实基线；未通过验证的评测器只能生成待审建议。

真实写入另建独立安全网关和审批状态机，至少按“只读观察 → 专业验证 → 人工审批 → 有限范围试运行 → 反馈检查”推进。未来写入工作流新增审批/下发/回执状态，不能将现有 `TRIAL_RUNNING` 解释为正在控制设备。模型无网关写权限，审批不能用 OpenCode 通用 `ask` 替代。

### 20.3 内网运行与制品

内网部署阶段再完成：模型 provider 替换、全部依赖锁定/离线缓存、OpenCode 二进制和配置、模型与必要权重、Adapter、数据库迁移、前端本地资源、哈希 manifest、安装与升级回退手册。禁用 CDN、在线下载、分享和更新，完成无外网安装与运行验收。

Demo 现在就保持 endpoint 配置化、前端资源本地打包、依赖可锁定；但不把模型下载、完整制品或断网演练列为首版完成条件。若后续无外网环境已有模型服务，无须重复打包部署该模型。

---

## 21. 测试方案

### 21.1 分层测试

| 层次 | 当前要求 |
| --- | --- |
| 单元 | 指标、Guard、状态迁移、停止优先级、配置、规范化 hash 和错误分类 |
| 契约 | Agent Schema、MCP 作用域、ObservationSource/CandidateEvaluator、OpenCode 固定版本、业务 API |
| 集成 | SQLite + Example + Fake Agent 的确定性全流程；真实 OpenCode/provider + 两 Agent + MCP 冒烟 |
| 恢复 | 外部请求前后、结果文件写入后、指标提交前后重启，以及迟到响应/取消竞态 |
| 页面/部署 | 触发、列表、曲线、取消、鉴权、内部路径不可访问、重启恢复、备份恢复 |

Fake 只用于测试分支控制，真实 OpenCode 冒烟不可省略。暂不要求 PostgreSQL、真实模拟器、现场设备或断网安装测试。

### 21.2 V1 必测场景

1. 正常稳定，Monitor 判断无需调参。
2. 偏高/偏低、超调、振荡、响应慢的固定曲线与指标单测；代表性的异常场景完成 Agent 全链路。
3. 数据缺失、冻结、时间错乱、单位/PID 快照不一致，Guard 阻断。
4. Monitor 不确定、规则冲突、低置信度，转人工。
5. 非法 JSON、多候选、非有限数、增量不一致、越界，格式修复/候选预算生效。
6. Control `ABSTAIN`，明确结束；重复相同候选不启动新评测。
7. 基线和候选不在同一评测上下文，拒绝比较。
8. 候选改善接受、恶化拒绝、硬约束失败拒绝；UI 显示建议而非下发。
9. 无改善、最大轮数、总超时分别停止；取消优先于迟到达标。
10. 相同幂等键同请求复用，同键不同内容报冲突。
11. 评测超时但已完成，通过查询恢复；失败与未知状态不盲目重跑。
12. 文件缺失/哈希错误/样本不足，不能进入评分。
13. 重复触发、同回路并发、重启后重复扫描，不创建两个活动 run。
14. OpenCode 中断、Agent 请求迟到、Session 丢失，不串用其他请求结果。
15. 后端在 job 派发、结果登记、接受提交附近退出，恢复后只有一个有效结果和一次接受事件。
16. 未授权工具、其他 run Token、过期 Token、路径穿越和 Prompt 注入均不能越权。
17. 浏览器未认证、CSRF、外部访问内部 API/OpenCode 端口被阻止。
18. 示例 Adapter 可被另一契约测试实现替换，Orchestrator/Agent/页面不修改；不使用真实写入驱动做此测试。
19. 本机重启服务后历史可查，数据库与文件备份可恢复。
20. 真实 OpenCode 两 Agent、各自 MCP 工具调用、示例程序、指标、网页端到端完成，且全部来源为 EXAMPLE。

### 21.3 回归方式

固定场景、种子、程序与指标版本，保存预期指标和合法停止状态。自动化测试用确定性 Fake Agent 保证可复现；真实 LLM 验收检查契约、边界、调用次数和合理结果，不要求逐字理由或完全相同 PID，不以偶然一次收敛证明控制有效。

---

## 22. 分阶段实施路线

### 阶段 0：环境核验与工程契约

- 核验 OpenCode 1.18.31、模型 provider/ID、Python/Node、端口、磁盘和常驻服务方式。
- 创建独立运行配置，启动受控测试实例，导出实际 `/doc`；用最小探针完成结构化输出与 Agent/Skill/MCP 权限冒烟，正式业务工具在阶段 2 实现。
- 建立 Python/前端工程与锁文件；实现领域 Schema、状态机、数据库迁移及 Adapter 接口。
- 记录域名、访问网段、证书来源和模型依赖；未提供模型时允许 Fake 单测推进，但真实链路验收保留为未完成。

**退出条件：** 环境依赖明确，契约可导出，数据库可迁移，Fake Agent 可走通基础状态迁移；真实 OpenCode 冒烟结果有记录。

### 阶段 1：示例程序与确定性闭环

- 实现 ExampleObservationSource、ExampleEvaluator 和固定入口程序，完成场景 manifest。
- 实现基线评测、参数 Guard、MetricsEvaluator、停止规则。
- 实现 job 登记、幂等、结果引用与恢复处理器，使用 Fake Agent 覆盖正常/异常/失败路径。

**退出条件：** 不依赖设备和模拟器，能从示例观测执行多轮候选、接受/拒绝、停止与重启恢复；相同请求重放结果一致。

### 阶段 2：OpenCode 双 Agent 与只读 MCP

- 实现 OpenCodeClient、两个 Agent、三个 Skill 和四个只读 MCP 工具。
- 加入调用 Token 作用域和权限验证、结构化校验、消息关联与有限重试。
- 将 Fake Agent 替换为真实 OpenCode；Control 只提候选，编排器提交评测。

**退出条件：** 真实 provider 完成 Monitor/Control + MCP + 示例评测全链路，有可追踪记录；模型越界无法改变业务状态或调用真实系统。

### 阶段 3：页面、本机发布和恢复验收

- 实现业务 API 与 Dashboard；补充手动/定时触发、轮询、曲线、取消和历史。
- 部署 systemd/Nginx，配置认证、TLS、CSRF 和内部路径隔离。
- 完成故障矩阵、备份恢复、启动检查与部署手册。

**退出条件：** 允许的客户端可访问本机业务服务；退出终端或重启组件不丢失已提交记录；第 24 节全部满足。本版在此完成。

### 后续 A：内网只读数据与适配器联调

按现场协议实现只读 Adapter，确定单位/PID 形式，建立真实数据契约与质量基准；根据需要迁移 PostgreSQL、接入专业评测器。

### 后续 B：无外网部署与事件触发

按环境准备离线制品并断网验收；有标注数据后再接小型识别网络。两项可独立推进，不依赖先开发真实写入。

### 后续 C：审批网关与受控下发

现场专业评审后实现真实写入契约、审批工作流、版本校验、回执与回退，单独制定生产验收。不能将 Demo 配置改成 real 就跳过该阶段。

---

## 23. 后续开发任务清单

按依赖顺序实施，避免先做网页假数据展示而没有业务状态源。

### 23.1 环境与契约

- [ ] 建立独立项目环境和依赖锁；固定 OpenCode 版本并导出 `/doc`。
- [ ] 核验实际模型能力与 `info.structured` 提取路径。
- [ ] 实现领域/Agent/Adapter/API Schema 与配置校验。
- [ ] 创建迁移、状态迁移表、回路唯一约束、lease 和幂等记录。

### 23.2 示例与业务内核

- [ ] 实现窗口/PID 快照、场景和确定性示例程序。
- [ ] 实现 CandidateEvaluator submit/get/cancel 及持久 job。
- [ ] 实现基线、参数 Guard、指标、接受与停止。
- [ ] 完成结果文件一致性、取消、超时、接管和恢复测试。

### 23.3 Agent 与工具

- [ ] 创建两个 Agent、三个 Skill、四个只读 MCP 工具。
- [ ] 实现默认拒绝权限、Token 作用域、只读内部 API 和审计。
- [ ] 实现 OpenCodeClient、请求关联、格式修复和迟到输出隔离。
- [ ] 使用真实 OpenCode/provider 完成全链路。

### 23.4 展示与部署

- [ ] 完成场景触发、详情、曲线、轨迹、取消、历史和定时设置。
- [ ] 生成前端类型与静态包，部署本机服务和反向代理。
- [ ] 验证认证、CSRF、内部接口隔离和 Secret 脱敏。
- [ ] 完成第 21 节矩阵、备份恢复、doctor 和运行手册。

---

## 24. V1 Definition of Done

本次 Demo 只有满足以下条件才算完成：

- [ ] 使用本机 OpenCode 的两个自定义 Agent 和真实配置 provider，Fake 不替代最终验收。
- [ ] 编排器显式控制角色顺序；Agent 无 task、shell、编辑、外网工具和真实控制权限。
- [ ] 所有 Agent 输出通过业务 Schema；Control 每轮只产生一个候选或 ABSTAIN。
- [ ] 数据库是权威状态源，同一回路无并发活动 run。
- [ ] 简单示例程序完成观测/基线/候选链路，无需模拟器或设备。
- [ ] 观测、评测、未来写入契约分开；更换只读/评测实现不修改核心工作流。
- [ ] 每轮最多一个有效候选结果，幂等冲突、未知状态和结果哈希均正确处理。
- [ ] Guard、接受、停止由程序控制；实际 PID 与推荐 PID 分离。
- [ ] 重启可恢复，已提交结果不重复接受；取消后迟到结果不能推进任务。
- [ ] 页面能显示来源、曲线、指标、Agent 理由、状态和错误，固定标记示例未连接设备。
- [ ] 本机常驻服务可从允许终端访问，认证生效，内部接口不对外暴露。
- [ ] 第 21 节的 V1 测试通过，有真实 OpenCode 冒烟和部署/恢复记录。
- [ ] 交付启动、配置、备份恢复与后续 Adapter 接入说明。

模拟器、生产控制阈值、真实 DCS 连接、离线模型包和断网生产验收均不属于此清单。

---

## 25. 常见错误实现与禁止事项

1. 以 OpenCode Session 代替数据库状态，或由 Monitor 通过 task 决定主流程。
2. 在工具或自然语言中直接更新 best、接受候选、改变参数边界。
3. 把真实下发隐藏在 CandidateEvaluator 中，或预埋可用的真实写入开关。
4. 将示例生成曲线解释为物理仿真、现场数据或已验证控制效果。
5. 混用观测 PID、当前 best 和最终推荐；忽略 PID 形式/单位差异。
6. 将不同来源/上下文的分数直接比较，或用缺失指标的零值绕过门禁。
7. 请求超时立即重跑，或声称跨所有外部系统有绝对 exactly-once 保证。
8. 使用任意 shell 命令作为示例执行接口，接受外部程序路径或结果路径。
9. 将 OpenCode、内部 API、模型凭据或调用 Token 暴露给浏览器。
10. 覆盖本机已有 OpenCode 配置、自动更新版本，或在权限失败后全局 allow。
11. 未做恢复与真实 OpenCode 验收就宣称全链路完成。
12. 将 PostgreSQL、消息队列、模拟器、完整离线制品提前设为首版前置条件。

---

## 26. 关键设计决策

| 决策 | 结果 |
| --- | --- |
| 保留 OpenCode 双 Agent | 验证指定技术路线中的状态解释与参数建议能力 |
| 编排器掌握所有业务副作用 | 模型输出可校验，状态与恢复点清晰 |
| Control 不自行评测 | 去掉工具写状态与 Agent 最终输出之间的双重提交问题 |
| ExampleEvaluator 先实现 | 降低 Demo 依赖，保留后续评测替换点 |
| 读数据/评测/真实下发分离 | 后续接入有边界，不能把执行示例误接成设备写入 |
| 单进程 Python + SQLite | 当前可在本机部署，减少运维组件 |
| 静态前端 + 反向代理 | 对外只提供业务入口，不公开 Agent 管理面 |
| 内网制品单独分期 | 当前先验证产品流程，同时保留配置化与离线可迁移性 |

---

## 27. 核验依据与仍需确认的事项

### 27.1 OpenCode 与依赖依据

已本机核验的事实见第 1.2 节；本地 SDK 类型文件位于 `/data9/xjx/.opencode/node_modules/@opencode-ai/sdk/dist/v2/gen/types.gen.d.ts`。它用于发现适配细节，**实际服务 `/doc` 与模型调用尚待阶段 0 验证**。

官方参考：

- [OpenCode Server](https://opencode.ai/docs/server/)、[SDK](https://opencode.ai/docs/sdk/)、[Permissions](https://opencode.ai/docs/permissions/)。
- [Agents](https://opencode.ai/docs/agents/)、[Skills](https://opencode.ai/docs/skills/)、[MCP](https://opencode.ai/docs/mcp-servers/)、[Config](https://opencode.ai/docs/config/)、[Providers](https://opencode.ai/docs/providers/)。
- [FastAPI](https://fastapi.tiangolo.com/)、[Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/)、[SQLAlchemy SQLite](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)、[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[Vite 静态部署](https://vite.dev/guide/static-deploy.html)。

### 27.2 实施前补齐但不影响当前接口设计的信息

| 信息 | 用途 | 未明确时的处理 |
| --- | --- | --- |
| provider/model/凭据和访问地址 | 真实 OpenCode 推理 | 可继续 Fake 单测；不宣称真实 Agent 验收完成 |
| 域名/IP、访问网段、证书、端口 | 本机对外服务 | 先回环联调，发布阶段明确配置 |
| systemd 方式、运行账户、磁盘类型 | 常驻与一致性存储 | 阶段 0 doctor 检查后生成部署文件 |
| 现场协议、压力单位、PID 形式/限值 | 后续内网 Adapter | 当前仅用明确的 Demo 类型，不猜测真实值 |

---

## 28. 最终交付口径

本版交付一个可在本机运行并对外提供业务页面的工程 Demo：OpenCode 双 Agent 提供状态解释与小步候选，确定性编排器通过简单示例程序完成评测与迭代，数据库保存业务状态，页面呈现曲线、建议和完整轨迹。

当前价值是验证业务流程及工程边界，后续通过观测与评测 Adapter 接入内网能力；真实控制写入需要独立审批与网关阶段。交付说明必须明确哪些结果来自示例、哪些能力尚未接入。
