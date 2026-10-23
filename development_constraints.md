# Demo 开发约束与实施顺序

## 内网系统可插拔接入增量（2026-09-24）

后续需求允许在原有 Example 工作流之外提供通用接入层：`TelemetryReader` 将真实系统的一致性快照映射为具名信号和可选参数集；`RuleMonitor` 快速判定信号质量、时效、阈值及变化速率；`Simulator` 以具名参数文件调用离线程序。参数集合不再限于 `kp/ki/kd`，但原网页演示、压力指标和 `EXAMPLE` 运行状态仍按本文件第 3–5 节执行。新接入层协议及当前集成边界见 [接入文档](docs/control-system-integrations.md)。真实设备写入仍不在范围内。

## 对话入口增量约束（2026-09-24）

- 新增 `reception` 角色，仅开放纯文本问答；不授予 MCP、文件、命令、网络、task 或控制权限。对话输入是非可信文本，不能修改检查规则和 PID 边界。
- 对话由后端持久化并按身份隔离；每轮独立 OpenCode Session，限制输入长度与携带的历史轮数。请求用 Idempotency-Key，未知模型结果只能查询对应 Session，不重复提交。
- 检查意图由后端确定性匹配动作词与对象词，显式按钮直接沿用 Trigger API。检查与调优只创建建议型 run，不能下发；模型问答不能代替权威 run 状态或指标。
- 对话展示公开的 Monitor/Control 输出、运行状态及审计事件；不展示隐藏推理或敏感 token。桌面和手机都要能完成发送、快捷检查、切换会话与查看控制台。


> 版本：V1.0 · 2026-09-22  
> 配套文档：[系统架构与实施计划](opencode_multi_agent_control_architecture.md)  
> 目的：让开发者知道先做什么、接口如何落地、异常如何处理、做到什么程度可以交付。

## 1. 使用规则与首版复杂度

架构文档解释系统意图和长期边界，本文约束当前 Demo 的实现。开发按本文阶段推进，不必先建齐架构文档里的所有目录和模块。文中的接口、字段和数值是本次开发基线，变更时同步契约与测试，不能只改 Prompt。

首版固定：一个本机后端进程、一个 Worker、一个演示回路、SQLite、本地结果文件、真实 OpenCode 双 Agent、简单示例程序和业务页面。保留观测、评测、未来真实写入的接口边界；只实现前两者的 Example 版本。不得增加设备驱动、模拟器、消息队列或通用工作流框架。

为降低实现成本，本文对架构计划作以下明确细化，首版以此为准：

| 项目 | 当前实现约束 | 以后再做 |
| --- | --- | --- |
| 实施顺序 | 先一轮完整链路与简易页面，再多轮/恢复，最后对外部署 | 不要求先完成全部故障治理才能接真实 Agent |
| 运行独占 | 本地进程锁 + DB 回路唯一约束 + `state_version` | 多实例 lease、心跳接管、fencing token |
| Session | 每次 Agent 请求尝试使用独立 Session，只提交一条业务用户消息 | 经验证后再复用同一 run 的角色 Session |
| MCP 调用授权 | 服务凭据 + 内存 Token 哈希/作用域，重启撤销全部旧 Token | 单独的持久化 call_context 表 |
| 忙时触发 | 相同场景合并，不同场景返回冲突 | pending_trigger 队列 |
| 观测接口 | 一次获取观测与 PID 的统一快照 | Adapter 内部按厂商协议拆分采集 |
| 示例数值 | 使用第 5 节已验证可达的场景及阈值 | 其他故障曲线、物理模型 |

独立 Session 的标题仍带 run/role/iteration/request ID；所有业务上下文由数据库重新组装，不依赖对话记忆。终态、默认拒绝权限、只读 MCP、确定性 Guard、示例来源标记和真实写入隔离保持不变。

**本轮只新增开发规格，不意味着 OpenCode 服务、模型、网页或部署已经验证。** 后续验收记录须区分“计划”“Fake 测试通过”“真实集成通过”。

## 2. 按什么顺序开发

每个阶段先完成最小验收，再继续下一阶段。环境缺少模型时可以推进确定性代码，但真实集成项必须保持未完成。

| 阶段 | 按顺序实现 | 阶段完成证据 |
| --- | --- | --- |
| D0：核验外部依赖 | 核验现有二进制/模型 → 临时专用 OpenCode 实例 → 一个结构化请求和只读 MCP 探针 → 导出 `/doc` | 一份脱敏兼容性记录，写清结构化字段、消息关联与 abort 的实际行为 |
| D1：可执行的契约和数据 | 第 3 节模型 → 第 4 节快照 → 第 5 节示例/指标 → SQLite 初始迁移 | 示例指标和好/坏候选测试通过，Schema 可导出 |
| D2：一轮完整链路 | 手动 Trigger → 快照/Monitor → 基线 → Control/Guard → 一次评测 → 持久化 → 简单详情页 | 用真实 OpenCode 两角色及只读 MCP 完成一轮，页面显示结果；先设 `max_iterations=1` |
| D3：多轮与基本恢复 | 循环/停止 → 幂等 → 取消 → 超时/重启扫描 → 历史列表 | 本文状态表和关键崩溃点测试通过；改为最多 5 轮 |
| D4：本机服务交付 | 定时触发 → 页面完善 → systemd → 反向代理/认证 → 备份恢复 | 允许的客户端可访问，重启后历史可查，操作与排障命令可复现 |

D0 不需要完整业务 MCP；使用测试探针即可，生产四个工具在 D2 实现。D1/D2 的 Fake Agent 仅用来稳定测试分支，不能作为 D2 的真实集成验收结果。

D2 页面先只做：选场景、开始、状态、压力曲线、实际/建议 PID、Agent 理由、错误。复杂布局、审计筛选、定时配置、人工复核放到 D4；不先制作与真实 API 无关的大量前端假数据。

代码按需放入架构计划中的 `apps/`、`packages/`、`examples/`、`tests/`。接口可以是 Python `Protocol` 加显式构造注入，禁止为两个 Example 类建设插件注册平台或动态加载系统。

## 3. 契约落地约束

### 3.1 通用规则

D1 在 `packages/contracts` 实现下列模型，并导出 JSON Schema；前端从后端 OpenAPI 生成类型。表中字段均必填，标 `?` 的字段可为 `null`，不能随意省略；另行注明默认值的请求字段例外。

- `ID`：服务端 UUID 字符串；OpenCode ID 是不透明字符串，格式由 Client 处理，不与业务 UUID 混用。
- `Time`：UTC、带 `Z` 的 ISO 8601 字符串；JSON 数值必须有限，禁止布尔值/数值字符串冒充数字。
- `PID`：`{kp: number, ki: number, kd: number}`，绝对范围和增量范围另由 Guard 校验。
- `source_kind`：`EXAMPLE | SIMULATION | REAL`，首版可实例化的 source/evaluator 只允许 EXAMPLE。
- 所有顶层请求/响应含 `schema_version: "1.0"`；内部引用的子对象不重复添加版本。
- 请求拒绝未知字段。生成类型与服务同时发布，V1 不做任意旧版本兼容转换。
- 所有返回数组有固定上限；大曲线使用不透明引用，Agent 不收到全部采样点。
- `request_id` 标识一次推理尝试；`iteration_id` 标识一次候选轮次，重提候选不创建第二个轮次。

### 3.2 数据模型

| 模型 | 必填字段及含义 |
| --- | --- |
| `SnapshotQuery` | `control_loop_id`、`as_of: Time`、`window_seconds: int > 0`、`request_id`、`scenario_id: string?`；Example 必须有注册场景，真实 Adapter 将来要求为空 |
| `PidSnapshot` | `pid: PID`、`pid_version: string`、`effective_at: Time`、`pid_form`、`parameter_units: {kp,ki,kd}`、`operating_condition_version: string` |
| `ObservationSnapshot` | `snapshot_id`、`control_loop_id`、`as_of`、`window_start`、`window_end`、`sample_period_seconds`、`source_kind`、`adapter_id`、`adapter_version`、`pressure_unit`、`operating_condition_version`、`pid: PidSnapshot`、`series: SeriesArtifact` |
| `SeriesArtifact` | `series_ref`、`sha256`、`sample_count`、`start_at`、`end_at`、`duration_seconds`；引用必须能关联所属 snapshot/job/run |
| `SeriesPoint` | `timestamp: Time`、`elapsed_seconds: number >= 0`、`pressure: number?`、`target_pressure: number`、`quality: GOOD \| MISSING \| BAD` |
| `QualityReport` | `valid: bool`、`sample_count`、`missing_count`、`bad_count`、`reason_codes: string[]` |
| `Metrics` | `metrics_version`、`signed_steady_error`、`steady_state_error`、`iae`、`overshoot_ratio`、`pressure_std`、`settling_time_s: number?`、`unavailable_reasons: string[]` |
| `EvaluationDecision` | `accepted: bool`、`target_reached: bool`、`hard_constraints_passed: bool`、`score: number?`、`reason_code`、`metrics: Metrics?` |

`pressure=null` 时 quality 必须为 MISSING/BAD；GOOD 必须有有限压力。V1 发现缺失/BAD 样本就阻断，不做插值。只有 `QualityReport.valid=true` 才计算完整 Metrics；质量不足直接记录原因，不构造填零的 Metrics。

曲线为 UTF-8 JSONL，每行一个 SeriesPoint：

```json
{"timestamp":"2026-09-22T00:00:00Z","elapsed_seconds":0,"pressure":30.4,"target_pressure":30.0,"quality":"GOOD"}
```

目标和单位来自冻结快照/上下文，不允许每个点自行改变。首版不提供流量、温度、控制输出等虚构字段，也不把缺失的控制输出指标计入分数。

### 3.3 候选评测模型

保留 `CandidateEvaluator.submit/get/cancel`，请求与结果由同一个契约包定义。

| 模型 | 必填字段及约束 |
| --- | --- |
| `EvaluationContext` | `context_id`、`snapshot_id`、`scenario_id/version`、`source_kind`、`adapter_id/version`、`program_version`、`target_pressure`、`pressure_unit`、`pid_form`、`start_at`、`duration_seconds`、`sample_period_seconds`、`seed`、`metrics_config`、`constraints`、`context_hash` |
| `TrialRequest` | `run_id`、`iteration_id?`、`candidate_id?`、`control_loop_id`、`trace_id`、`purpose: BASELINE \| CANDIDATE`、`idempotency_key`、`candidate_pid`、`evaluation_context_ref`、`evaluation_context_hash`、`request_hash`、`timeout_seconds` |
| `TrialRecord` | `trial_id`、`request: TrialRequest`、`status`、`source_kind`、`adapter_id/version`、`created_at`、`started_at?`、`finished_at?`、`series: SeriesArtifact?`、`error: ErrorDetail?` |
| `ErrorDetail` | `code`、`message`、`retryable: bool`、`trace_id` |

BASELINE 的 iteration/candidate ID 必须为空；CANDIDATE 必须关联已经通过 Guard 的唯一候选。只有 COMPLETED 有非空 series；FAILED 必须有 error；查询不存在返回明确 `NOT_FOUND`，连接错误绝不能转换成 NOT_FOUND。

`submit()` 和 `get()` 均返回 TrialRecord；`cancel()` 也返回最新 TrialRecord，不必再建立三种几乎相同的 receipt 类型。状态沿用 `QUEUED | RUNNING | COMPLETED | FAILED | CANCELLED | UNKNOWN`；UNKNOWN 表示调用方无法确认，不得覆盖已经可靠持久化的终态结果。

V1 规范化 hash：只接受本项目契约中的 JSON 基础类型；PID 在 Guard 检查原值后规范化到 6 位小数，超出精度拒绝而非静默修正；有限数转换成确定的十进制表示，键排序，UTF-8，无额外空白。`1` 与 `1.0`、`-0.0` 与 `0.0` 归一为同值，禁止非有限数；以测试向量冻结实现，不要求开发通用跨语言签名标准。

- context hash 不含自身/`context_id`，包含场景、单位、程序、初始快照等实际评测条件，不含候选 PID。
- request hash 对 TrialRequest 去掉自身 hash、trace_id 后计算；重试不改变 timeout 等其他字段。
- API 幂等 hash 仅覆盖验证后的用户请求体，不能包含每次请求新生成的服务 ID/时间。
- 同键不同 hash 为 `IDEMPOTENCY_CONFLICT`；浮点容差不用于判定同一幂等请求。

### 3.4 Agent 模型

请求公共字段：`schema_version`、`request_id`、`run_id`、`control_loop_id`、`source_kind`、`call_context_token`。Token 不写入日志、页面或长期请求正文存档；存档时以占位符替换，并单独保存 Token 哈希/作用域。

| 模型 | 其他必填字段 |
| --- | --- |
| `MonitorRequest` | `snapshot_id`、`target_pressure`、`pressure_unit`、`actual_pid: PidSnapshot`、`quality: QualityReport`、`metrics: Metrics`、`rule_assessment`、`policy` |
| `MonitorOutput` | `assessment`（架构中的枚举）、`confidence: 0..1`、`needs_tuning: bool`、`manual_review_required: bool`、`evidence: [{feature,value,interpretation}]`、`reason`、`warnings: string[]` |
| `ControlRequest` | `iteration_id`、`candidate_id`、`actual_pid`、`current_best: {pid,metrics,score}`、`evaluation_context_hash`、`constraints`、`remaining_iterations`、`history`、`example_program_description` |
| `ControlOutput` | `decision: PROPOSE \| ABSTAIN`、`delta: PID?`、`candidate_pid: PID?`、`reason`、`expected_effect`、`risk_notes: string[]` |

`rule_assessment` 是 `{assessment, reason_codes}`；`policy` 明确 Monitor 允许类别、置信度阈值和目标阈值；`constraints` 固定为每参数 min/max/max_delta 与 equality_tolerance。history 最多 5 条，每条 `{iteration_no, candidate_pid, metrics?, score?, accepted, reason_code}`。`example_program_description` 为服务端固定的示例函数说明，不能由用户覆盖。

PROPOSE 必须有 delta/PID，ABSTAIN 两者为空。输出不得含状态更新、trial ID 或“已应用”字段。字符串长度限制：reason/expected_effect/interpretation 各不超过 2000 字符，warnings/risk_notes/evidence 各最多 10 项。Monitor 的 needs_tuning 与 assessment 冲突时转人工，不挑一个字段继续。

### 3.5 首批 API 与响应

D2 只实现以下接口，其余架构路由到 D3/D4 再补；未实现路由返回 404，不做假成功占位。

| API | 请求与响应 |
| --- | --- |
| `GET /api/v1/scenarios` | `{schema_version, items: [{scenario_id, name, description, source_kind}]}`，仅注册场景 |
| `POST /api/v1/triggers` | 请求 `{schema_version, scenario_id}`；回路由首版服务配置确定；响应 202 `{schema_version, trigger_id, run_id, coalesced, status_url}` |
| `GET /api/v1/runs/{id}` | `RunDetail`，见下文 |
| `GET /api/v1/series/{ref}` | `{schema_version, series_ref, source_kind, pressure_unit, original_sample_count, returned_sample_count, downsampled, points: SeriesPoint[]}` |
| `GET /api/v1/health` | `{schema_version, live, ready, degraded, components: [{name, ready, version?, error_code?}]}` |

RunDetail 必含 `run_id`、`control_loop_id`、`scenario_id`、`status`、`state_version`、`source_kind`、`created_at`、`finished_at?`、`actual_pid?`、`best_pid?`、`best_metrics?`、`best_score?`、`observation_series_ref?`、`baseline_series_ref?`、`monitor_output?`、`iterations`、`stop_reason?`、`error?`。

iterations 每项为 `{iteration_id, iteration_no, candidate_pid?, trial_status?, series_ref?, metrics?, score?, accepted?, reason_code?, control_output?}`；`accepted=null` 表示尚未裁决，false 表示已拒绝。最多 5 轮，首版详情一次返回即可。后续历史列表使用 `items + next_cursor`，不要强加全站分页框架。

所有公共 API 的错误响应为 `{schema_version, error: ErrorDetail}`，FastAPI 默认验证错误也转换到此结构。写请求遵守架构中的认证、CSRF 和 Idempotency-Key 规则；回环开发可启用明确的本地开发身份，D4 对外发布前必须关闭该模式。D2 series 最多 1000 点直接返回，D4 才需要降采样。

## 4. 缺口一：观测与 PID 必须来自同一快照

对外只保留以下业务接口，替代分别读取窗口和“当前 PID”的调用方式：

```python
class ObservationSource(Protocol):
    async def read_snapshot(self, query: SnapshotQuery) -> ObservationSnapshot: ...
    async def health(self) -> ComponentHealth: ...
```

首版不必开发 capabilities 发现服务，能力作为 Adapter 的静态元数据提供；真实 Adapter 以后实现同一 Protocol。

快照规则：

1. Example 以 `scenario_id/version + as_of + window_seconds` 冻结场景，观测曲线采用场景 initial_pid；snapshot 内 PID 与生成曲线使用的参数完全一致。
2. 固定 `window_end=as_of`、`window_start=as_of-window_seconds`；121 点对应 120 秒，包含两个端点。
3. PID 的 effective_at 不晚于窗口起点，参数版本在整个窗口有效；工况版本与窗口一致。
4. 首版不处理窗口中途调参/切工况，发现多版本则 `SNAPSHOT_MISMATCH` 转人工；未来可拆窗口，但不能默默拼接。
5. Guard 后快照不可变；恢复沿用原快照。人工重新检查创建新 run，使用新的 as_of。
6. 将来现场不支持原子读时，由 Adapter 读取前后版本核对并校验历史生效区间；无法确认一致性就拒绝，不能标注为可靠快照。

只读 MCP 的窗口和 PID 都来自该快照/DB，不重新读取一次“实时当前值”。`get_tuning_context` 同时区分 actual_pid 与 best_pid。

## 5. 缺口二：固定一个确实可改善的示例

### 5.1 示例函数与边界

本示例仅证明工作流，不模拟真实 PID 物理规律。配置固定为：

```yaml
scenario_version: examples-v1
source_kind: EXAMPLE
target_pressure: 30.0
pressure_unit: demo_unit
initial_pid: {kp: 1.0, ki: 0.2, kd: 0.05}
reference_pid: {kp: 1.1, ki: 0.2, kd: 0.05}
pid_scale: {kp: 0.1, ki: 0.05, kd: 0.02}
duration_seconds: 120
sample_period_seconds: 1
steady_tail_fraction: 0.2
settling_band_absolute: 0.1
settling_hold_seconds: 10
hard_pressure_bounds: {min: 29.0, max: 30.8}
targets:
  steady_state_error: 0.08
  overshoot_ratio: 0.02
  pressure_std: 0.02
  settling_time_s: 40.0
min_improvement: 0.001
```

PID 绝对/步幅边界沿用架构第 16 节。为消除原示例的不可达风险，明确固定残差的最小值和目标阈值：

```text
t = 0, 1, ..., 120
q = 1 + Σ((pid[k] - reference_pid[k]) / pid_scale[k])²
pressure(t) = 30 + q × (0.05 + 0.15 × exp(-t / 10))
```

`q=1` 时末段误差约 0.05，小于 0.08 的目标，故有可达解。观测与候选都使用此函数；无随机项，seed 字段保留为 0。示例函数说明及 reference_pid 可以明确提供给 Control，帮助演示稳定完成；这不构成盲测或控制优化能力证明，不要求模型靠真实 PID 经验猜中人为函数。

### 5.2 指标必须精确定义

- 末段取最后 `ceil(N × 0.2)` 个样本，N=121 时取 25 个。
- `signed_steady_error=mean(tail)-target`；`steady_state_error=abs(signed_steady_error)`。状态偏高/偏低用有符号值，评分用绝对值。
- IAE 对实际 elapsed_seconds 用梯形积分；std 使用总体标准差（ddof=0）。
- 超调沿用架构公式；本例目标为正且固定。
- 稳定时间取最早满足“此后所有样本均在 ±0.1 内，且剩余观测时长至少 10 秒”的采样时刻；无此时刻返回 null。不能只观察到短暂 10 秒稳定就忽略后续偏离。
- 分数沿用架构权重/尺度：`J=0.4E + 0.2(O/0.1) + 0.2S + 0.2(T/120)`；T 为 null 时评分代入 120，但达标判定仍为 false。
- 计算比较保留原始精度，仅展示时四舍五入；数值容差只用于浮点等式和单测，不将真实超限改成通过。

### 5.3 最小场景与已核算结果

| 场景/候选 | 设定 | 预期 |
| --- | --- | --- |
| `normal` | initial_pid 改为 reference_pid | 规则正常，Monitor 正常时 NO_TUNING_REQUIRED |
| `improvable` 基线 | PID=(1.0, 0.2, 0.05) | 误差约 0.10000784，稳定时间 null，J≈0.26667088，未达标 |
| 改善候选 | 基于上述基线，Kp 增加 0.1 | 误差约 0.05000392，稳定时间 11 秒，J≈0.05166877；接受且 SUCCEEDED |
| 恶化候选 | 基于上述基线，Kp 减少 0.05 | 误差约 0.16251274，J≈0.30834018；硬边界通过，但拒绝 |
| 不安全候选 | 基于上述基线，Kp 减少 0.1 | 参数 Guard 通过，但峰值 31 超过 30.8；拒绝并转人工 |
| `no_improvement` | 无论候选如何都返回 q=2 曲线 | 用三个不同合法候选触发连续无改善停止 |

这些数值已用独立 Python 计算复核，单测对展示值允许 `1e-6` 绝对误差。无需为每条候选创建新场景。Fake Agent 测试分别固定好/坏候选，真实 Agent 验收只要求合法行为与合理停止，不保证每次一轮成功。

`data_missing` 仅在测试中将一个点设为 null/MISSING；超时、进程失败、文件哈希错误通过测试替身注入，不增加普通用户配置入口。

首版规则识别先只实现：质量失败 DATA_ANOMALY；末段有符号误差超过 ±0.08 为 STEADY_HIGH/LOW；全部目标满足为 NORMAL_STABLE；其余为 UNCERTAIN。完整枚举保留，振荡等高级分类后续再补，不为凑齐分类训练模型。Monitor 与规则冲突转人工；其 confidence 低于 0.6 也转人工。

## 6. 缺口三：状态与异常不能由开发者各自解释

### 6.1 统一裁决

先明确几个容易混淆的结果：

| 情况 | 首版确定行为 |
| --- | --- |
| 提议参数越界、增量不一致、重复已登记参数 | 未登记新候选、未执行评测；同轮允许一次业务重提，仍失败则转人工 |
| 格式不合法 | 同一角色/轮次最多一次格式修复，仍不合法则 FAILED_FINAL / AGENT_OUTPUT_INVALID |
| 候选结果完整、安全，但分数没有足够改善 | CANDIDATE_REJECTED，保留 best；可以进入下一轮 |
| 候选曲线触发压力硬边界 | 保存拒绝决定与 CANDIDATE_REJECTED 事件，并在同一事务将 run 转 MANUAL_REVIEW_REQUIRED；不继续尝试 |
| 基线本身触发硬边界 | MANUAL_REVIEW_REQUIRED / BASELINE_UNSAFE，不进入 Control |
| 曲线文件损坏、hash/上下文不符 | FAILED_FINAL / RESULT_INTEGRITY_ERROR，不当作普通“效果差”继续 |
| 数据质量不足、Monitor 冲突/不确定、Control ABSTAIN | MANUAL_REVIEW_REQUIRED，记录具体 reason_code |
| 基础设施明确失败，有限重试耗尽 | FAILED_FINAL；结果是否执行仍不明确则 MANUAL_REVIEW_REQUIRED / RESULT_UNKNOWN |

因此“候选越界”“运行得到不安全曲线”“结果损坏”是三类错误；状态表、测试和页面必须一致。

### 6.2 正常迁移表

沿用架构中的 run 状态枚举。表中“提交”表示一个短事务内写状态/state_version、相关记录和事件；不在事务内等待网络或子进程。

| 当前状态 | 事件/条件 | 下一状态 | 同时提交的业务记录 |
| --- | --- | --- | --- |
| CREATED | Worker 领取 | DATA_LOADING | started_at、snapshot 请求参数 |
| DATA_LOADING | 快照有效 | MONITOR_REQUESTED | 快照/质量/指标、agent_request |
| MONITOR_REQUESTED | 有对应合法最终响应 | MONITOR_COMPLETED | Monitor 输出、请求完成标记 |
| MONITOR_COMPLETED | 规则与 Monitor 一致且正常 | NO_TUNING_REQUIRED | stop_reason、finished_at |
| MONITOR_COMPLETED | 规则与 Monitor 一致允许调参 | BASELINE_REQUESTED | EvaluationContext、唯一基线 job |
| BASELINE_REQUESTED | 已派发、确认 job 已登记 | BASELINE_RUNNING | trial_id、attempt、开始时间 |
| BASELINE_RUNNING | 结果完整有效 | BASELINE_COMPLETED | series 引用、基线指标、best=实际初始 PID |
| BASELINE_COMPLETED | 基线达标 | SUCCEEDED | 最终建议、stop_reason |
| BASELINE_COMPLETED | 安全但未达标 | CANDIDATE_REQUESTED | iteration/candidate 预分配 ID、Control 请求 |
| CANDIDATE_REQUESTED | PROPOSE 且 Guard 通过 | CANDIDATE_VALIDATED | 不可变候选内容、Control 输出、请求完成标记 |
| CANDIDATE_VALIDATED | 准备评测 | TRIAL_REQUESTED | 唯一候选 job、request hash、幂等键 |
| TRIAL_REQUESTED | 已派发、确认 job 已登记 | TRIAL_RUNNING | trial_id、attempt、开始时间 |
| TRIAL_RUNNING | 结果完整有效 | TRIAL_COMPLETED | series 引用、hash、job 完成状态 |
| TRIAL_COMPLETED | 准备算分 | EVALUATING | 评测版本；已有合法结果不再执行程序 |
| EVALUATING | 安全且满足改善条件 | CANDIDATE_ACCEPTED | 指标/score、accepted=true、更新 best、无改善数清零 |
| EVALUATING | 安全但无改善 | CANDIDATE_REJECTED | 指标/score、accepted=false、best 不变、无改善数+1 |
| CANDIDATE_ACCEPTED / CANDIDATE_REJECTED | 满足停止条件 | 对应终态 | stop_reason、finished_at |
| CANDIDATE_ACCEPTED / CANDIDATE_REJECTED | 仍允许迭代 | CANDIDATE_REQUESTED | 下一 iteration/candidate/request |

同步快速完成的 job 仍按顺序提交 REQUESTED/RUNNING/COMPLETED，不为同步与异步维护两套状态。BASELINE_RUNNING 可以查询到已完成结果；恢复时不会因为未观察到真正运行过程而重新执行。

### 6.3 跨状态规则和预算

- 任意非终态接收到 cancel：先提交 `cancel_requested=true`；之后所有结果提交检查该字段并转 CANCELLED，不能因同时达标而 SUCCEEDED。
- 总截止时间按 started_at + 900 秒固定，重试/重启不重置；到期 STOPPED_TIMEOUT。若已经可靠记录完整性/硬边界故障，保留相应故障终态，不被超时覆盖。
- 结果硬边界/完整性故障优先于普通达标；已取消永远优先。正常停止顺序：**当前 best 达标 → 连续无改善 3 轮 → 已执行 5 轮**。
- 只有接受后 best 达标才能 SUCCEEDED；一个未被接受的候选即使局部指标达标，也不能把 run 标为成功。
- 同轮格式修复最多 1 次，业务候选重提最多 1 次；总 Control 尝试最多 3 次（首次+各一次）。修复请求是新 request_id/Session，仍属于同一 iteration。
- 普通效果拒绝消耗轮次且累计无改善；参数非法重提不消耗有效评测轮次，但消耗尝试预算。预算耗尽明确结束，不能无限循环。
- 明确可重试的基础设施失败最多重试 1 次，间隔 2 秒；超时未知的推理/评测不进入这条盲重试规则。
- 基础设施重试也占用该角色的尝试总上限：Monitor 最多 2 次、每轮 Control 最多 3 次，不能叠加不同重试原因突破上限；只读状态查询不计模型尝试次数，但受查询/总耗时预算限制。
- 终态没有出边；retry/manual recheck 创建 parent_run_id 关联的新 run。
- 全部未列出的状态迁移报 RUN_STATE_CONFLICT；同事件重放只在对应业务记录和 hash 一致时幂等返回。

迁移表实现为枚举 + 处理函数白名单即可，无须引入状态机框架。测试应覆盖上述业务分支和事务结果，不为每个枚举字符串单独写无意义测试。

## 7. 缺口四：OpenCode 请求可找回，找不回就明确停止

### 7.1 已知依据与待验证项

本机 1.18.31 SDK v2 类型包含请求 `messageID`、Assistant 的 `parentID` 和 `structured`，但类型存在不等于重复提交一定幂等。[官方 Server 文档](https://opencode.ai/docs/server/)提供 Session 消息查询、状态查询和 abort 接口；具体完成语义、ID 格式及结构化输出以本机 `/doc` 和 D0 探针为准。

D0 必须记录四项：

1. 能否提交自定义 messageID，其合法格式和响应 parentID 如何关联。
2. 工具调用期间是否有多条 assistant 消息；如何识别最终 `info.structured` 而非中间消息。
3. HTTP 客户端超时后 Server 是否继续运行，能否从消息查询找回结果。
4. abort 返回后如何确认 Session 已停止；不能把 HTTP 200 当作“推理已完全停止”的唯一证据。

记录写入 `docs/runbooks/opencode-compatibility.md`，保存脱敏请求/响应和版本。禁止为了通过探针开放所有工具权限，禁止读取/输出现有模型密钥。

### 7.2 首版请求算法

不依赖 messageID 作为服务端幂等能力，使用“一次尝试一个 Session”限制不确定性：

1. 创建 agent_request，保存业务 request_id、角色、轮次、脱敏 payload hash、deadline，状态 PREPARING。
2. 创建独立 Session，标题带 request_id；拿到 session_id 后**先提交数据库**。尚未成功绑定 Session 前绝不发送 Prompt。
3. 如果创建 Session 的响应丢失，可能留下空孤立 Session；可以重新创建空 Session，不将它当作已执行推理。无需为清理空 Session 阻塞业务。
4. 生成作用域 Token，提交 agent_request 为 REQUESTED，再发送唯一业务 Prompt。若自定义 messageID 已通过 D0 验证，则在发送前持久化它；否则依靠唯一 Session + 唯一业务用户消息定位。
5. 校验响应的 Session、role、parentID（可用时）、最终完成状态、无错误和 info.structured；通过 Schema 后将输出与业务迁移同事务提交。
6. 禁止往该 Session 发第二条业务 Prompt；修复/重提/安全重试使用新 request_id 和 Session，显式携带原错误与权威摘要。

首版使用同步 HTTP Prompt 即可，不强制增加 SSE 或异步事件消费者。实际 request timeout=120 秒，总 run deadline=900 秒；响应丢失后每 2 秒查询一次，最多 30 秒且不得超过 run deadline。查询只读，可重试；不重新 POST 原 Prompt。

### 7.3 请求失败/恢复决策

| 观察结果 | 行为 |
| --- | --- |
| DB 已有完成响应/裁决 | 直接复用，不调用模型 |
| Session 有唯一对应最终合法输出，且未取消、未终止、未过 deadline | 校验后提交，恢复正常流程 |
| Session 仍 busy | 在查询预算内等待；到期尝试 abort，撤销 Token |
| Session idle，有明确错误且无有效最终输出 | 记录失败；确定旧尝试结束后按对应预算修复或重试 |
| Session idle，没有用户消息或最终结果 | 不能仅凭空查询判定此前 POST 没执行；转人工 RESULT_UNKNOWN，不自动补发 |
| Session 不可达/丢失、存在无法解释的多条用户消息/多个最终输出 | 转人工 RESULT_UNKNOWN；保存能找到的证据，不取“最后一条”猜测 |
| 请求已过期、run 已终态或 Token 已撤销后输出到达 | 只记录 LATE_RESULT，不更新候选/best |

只有“明确在发送前失败”，或“Server 确认该尝试结束且无有效结果”的情况可以创建替代请求；网络中断、空响应、abort 已发出都不能单独证明可安全重试。

独立 Session 中可以有多条工具中间消息；有效最终输出的识别由 D0 验证后封装在 Client 中。消息查询若有分页，遍历到该请求的用户消息为止，不能只查询第一页便认定不存在。

此方案允许无法判断的少数任务转人工，用户通过 retry 创建新 run。首版无需实现复杂的 OpenCode 消息重放协议，也不承诺模型推理物理执行绝对一次。

## 8. 缺口五：把契约变成可测试的交付物

第 3 节定义的字段不是让开发者继续编写另一份长文档。D1 必须交付实际模型、合法/非法 fixtures 和导出的 Schema：

```text
packages/contracts/observations.py
packages/contracts/trials.py
packages/contracts/agents.py
packages/contracts/api.py
tests/fixtures/contracts/
tests/fixtures/scenarios/
scripts/export_contracts.py
```

开发约束：

- Snapshot、Trial、Agent、RunDetail 各至少一个可校验 fixture；对应缺字段、错误枚举、非法空值/非有限数的失败用例。
- 可通过 `python scripts/export_contracts.py` 重建 `docs/api/`；生成文件禁止人工改，变更模型后更新生成结果。
- 候选 PID、指标、状态使用同一领域类型；Repository 不直接接受无约束 dict，避免数据库出现另一套字段名。
- API 只传业务结果，内部文件路径、服务 Token、模型凭据不进入响应模型。
- 替换 Adapter 的契约测试使用第二个极简测试实现，不要求额外进程/网络协议；证明编排器不依赖 Example 私有属性即可。

## 9. 持久化和恢复只做必要部分

### 9.1 单进程约束

本机 state_dir 必须在支持文件锁和 SQLite 语义的存储上。后端启动时用持久打开的文件描述符取得 `flock` 排他锁，进程生命周期内一直持有；第二个实例直接退出。进程退出由 OS 释放锁，不通过“删除锁文件”接管。

在这个前提下，启动后的 Worker 可以扫描旧非终态 run，不需要 owner lease/fencing。DB 仍保留 `state_version` 的 CAS 更新和活动回路部分唯一索引，用来防 API 取消、后台执行、重复触发间的竞态。运行限制必须写入 README/systemd：一个 Uvicorn worker、不开 reload。

### 9.2 最少存储对象

首批迁移只建 run、iteration、trial_job、agent_request、audit_event、series_artifact、trigger_event/api_idempotency 必需表；不为每个 DTO 都建独立表。Monitor 输出/配置快照可以作为 JSON 列存入 run。调用 Token 存内存中的哈希作用域映射即可，重启全部失效；恢复正在使用旧 Token 的推理时尝试 abort 并转人工，新尝试重建授权。

重启后先查询旧请求：若已经有完整合法的最终输出，可在未超时/取消的前提下按第 7 节恢复提交；若仍在推理，则按上述规则撤销旧授权并停止该 run。确认已停止前，不向同一 run 另发推理请求。

job 派发前入库；每个 job 有独立目录，子进程只写临时结果文件。后端校验后原子改名，再提交 series 引用。程序无 DB 写权限；MCP 只调用受限内部只读 API。读取旧请求时不能将旧 Token 从日志中恢复出来。

文件落盘和 DB 不存在共同事务，恢复规则固定为：

| 崩溃点 | 重启行为 |
| --- | --- |
| job 登记前 | 按现有 run 状态创建唯一 job |
| job 已登记但未派发 | 确认没有遗留示例进程后，用相同 job/幂等键执行 |
| 示例执行中 | systemd 停止整个服务进程组；开发启动器也管理子进程组。无法证明旧进程已停止则转人工，不凭 PID 数字直接杀进程或认定存活 |
| 完整结果文件已落盘但 DB 未登记 | 根据 job manifest/request hash 验证并登记，不重算 |
| 只存在临时/不完整文件 | 确认原进程已结束后重算确定性示例，沿用 job、递增 attempt，最多一次 |
| 结果已登记但未裁决 | 重算指标并 CAS 提交裁决 |
| best 和接受事件已提交 | 直接推进下一状态，不重复计数/接受 |

结果文件单独不够，必须有服务端 job manifest 将文件与请求关联；manifest/结果由受控目录解析。迟到文件不能覆盖已经登记的有效结果。子进程物理重算可以发生，业务上只有一个有效结果与一次接受提交。

### 9.3 触发与取消

首版同回路忙时，新触发只合并到当前 run，返回 `coalesced=true`；不实现额外 pending_trigger 队列。不同 scenario 的并发新触发返回 409，避免用户以为正在运行所选场景。定时 tick 在忙时记录合并即可，下一个时间槽再触发；关机期间不补跑历史 tick。

取消先写 DB 标记再执行 abort/cancel；向浏览器返回“取消处理中”时使用原 status + cancel_requested，不增加任意新状态字符串。后台最终提交 CANCELLED 后，即使文件/模型响应迟到也不能更改结果。

## 10. 每个阶段应验证什么

无需一开始写齐架构的所有测试。测试随对应能力进入阶段，但不得用手工截图替代业务一致性测试。

| 阶段 | 必须通过的检查 |
| --- | --- |
| D0 | 真实结构化输出、只读工具、默认拒绝权限、消息关联、超时查询/abort 探针 |
| D1 | Schema fixtures；窗口/PID 一致性；正常/改善/恶化/不安全/无改善示例；指标和 hash 测试向量 |
| D2 | Fake 分支集成 + 真实 OpenCode 两角色各自工具调用；一轮结果入库并由页面读取 |
| D3 | 无效输出/重提预算、三个停止条件、重复触发、同键异内容、取消竞态、上述崩溃点、未知结果转人工 |
| D4 | 未认证访问/CSRF/内部 API 隔离；定时任务去重；退出终端常驻；DB+曲线备份恢复 |

每阶段记录实际命令和结果，建议统一：`pytest`、`ruff check`、前端 typecheck/build。具体包管理脚本随工程创建，但禁止在失败时只改验收文案绕过检查。Prompt/Skill 修改至少重跑 Agent 契约与代表性真实集成；纯页面布局变化不必重跑全部故障注入。

## 11. 首版交付与日常开发规则

首版完成条件为：D0–D4 的证据齐全，真实 OpenCode 全链路可展示，多轮有界、错误可解释、已提交结果不重复接受，本机允许终端能访问，备份可恢复。架构文档中的多实例治理、完整识别分类和扩展基础设施不作为本版门槛。

- 不覆盖 `/data9/xjx/.opencode` 中现有配置或凭据；使用已安装的 `/data9/xjx/.opencode/bin/opencode` 和项目独立配置。
- 模型/工具不可用时明确失败，不能静默切换 Fake。测试模式必须与服务模式显式区分。
- 所有建议保持在 run 内，UI 固定显示“示例数据 / 未连接真实设备”。不出现“设备参数已应用”。
- 前端不直接连 OpenCode；Agent 无 shell、编辑、task、真实写入能力；候选评测只由编排器调用。
- 参数边界、场景、程序版本和指标配置随 run 冻结；Prompt 无权改变它们。
- D2/D3 可以仅回环开发；D4 对外发布前落实架构中的认证/TLS/CSRF、内部路径隔离，不增加企业 SSO 或复杂角色系统。
- README 至少说明启动、停止、模型配置、演示步骤、测试、日志、备份/恢复；真实控制系统相关能力明确标为未接入。
- 任何首版范围增加都需说明它解决当前哪个可复现问题。不能因为“以后可能需要”引入新的服务或抽象层。
