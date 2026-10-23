# 接入内网控制系统与离线模拟器

当前网页有两条独立流程：`EXAMPLE` 虚拟压力回路供 Monitor 冻结窗口并诊断；`SIMULATION` 汽水循环 B 由操作员单独启动，Control 基于逐工况仿真历史提出给水泵 PID 候选。B 的内置简化模型尚未用现场数据校准，也没有设备写入接口。下面的接入层为以后连接真实系统准备；没有厂商协议和字段表时，不会假装已连接。

## 1. 观测适配器

接入程序实现 `TelemetryReader.read_frame(control_loop_id)`，返回 `TelemetryFrame`：同一时刻的具名信号、当前参数集及参数版本。`MappedTelemetryReader` 可把已有内网程序返回的一份嵌套 JSON 映射成这个模型；例如厂商字段 `sensor.pressure` 映射为内部信号 `pressure`，`tuning.gain` 映射为参数 `gain`。字段缺失、非法数值、回路 ID 不一致会直接报错。

只有状态监测需求时可以不配置可调参数；要评测候选时必须提供完整的参数集和版本。

```python
from packages.adapters.telemetry import (
    MappedTelemetryReader, MonitorRules, PayloadMapping, RuleMonitor, SignalRule,
)

# fetch 是接入方自己的异步函数；可调用内网 API、读本地 IPC 或厂商 SDK。
reader = MappedTelemetryReader(fetch, PayloadMapping(
    observed_at="meta.timestamp",
    control_loop_id="meta.loop_id",
    parameter_version="meta.parameter_version",
    parameters={"gain": "tuning.gain", "ramp": "tuning.ramp"},
    signals={"pressure": "sensors.pressure", "temperature": "sensors.temperature"},
    signal_units={"pressure": "bar", "temperature": "C"},
    signal_quality={"pressure": "sensors.pressure_quality"},
))
monitor = RuleMonitor(MonitorRules(
    signals={
        "pressure": SignalRule(unit="bar", minimum=29.0, maximum=31.0,
                               max_rate_per_second=0.5),
        "temperature": SignalRule(unit="C", maximum=80.0),
    },
    max_age_seconds=30.0,
))
frame = await reader.read_frame("pump-a")
result = monitor.check(frame)  # NORMAL / ABNORMAL / UNKNOWN，含逐信号原因码
```

`RuleMonitor` 是确定性、按字段线性扫描的快速判定；缺失、坏质量、单位不符、过期或时间倒退返回 `UNKNOWN`，上下界和变化速率越限返回 `ABNORMAL`。跨参数版本不计算变化速率。阈值与单位由接入方配置，不由 Agent 修改。若厂商接口不能一次读取一致的信号和参数，接入方必须在 `fetch` 中做读前/读后版本核对；不能确认一致性就应报错，不拼接成可靠快照。

观测帧携带 `source_kind`，映射器默认标为 `REAL`；离线模拟器结果固定标为 `SIMULATION`，两者在调用方展示和评分时应分开处理。

## 2. 离线模拟器适配器

`OfflineProcessSimulator` 调用运维人员预先配置的本地可执行程序。输入文件包含 `request_id`、`control_loop_id`、`scenario_id`、`parameters`、`parameter_version`、`context` 和 `input_sha256`；参数是任意 1–32 个具名有限数值，不要求 `kp/ki/kd`。程序收到两个附加命令行参数：输入 JSON 文件路径、输出 JSON 文件路径。它应把结果写成：

```json
{
  "request_id": "与输入一致的 UUID",
  "objective": 0.12,
  "metrics": {"steady_error": 0.03},
  "samples": [
    {"elapsed_seconds": 0.0, "signals": {"pressure": 30.2}},
    {"elapsed_seconds": 1.0, "signals": {"pressure": 30.1}}
  ]
}
```

```python
from pathlib import Path
from packages.adapters.offline_simulator import OfflineProcessSimulator

simulator = OfflineProcessSimulator(
    ("/opt/simulator/bin/simulate", "--offline"),
    Path("/var/lib/control-demo/simulation-jobs"),
    timeout_seconds=120,
)
```

调用方只把候选参数写入输入 JSON；模拟器命令、工作目录和超时由部署配置固定。执行不经 shell，不需要公网；输出会校验 UUID、数值、样本顺序与文件大小。同一 `request_id` 的重试核对输入哈希和结果回执后复用结果；旧子进程还在运行或结果不完整时明确报错，不盲目重跑。每次评测使用独立目录，便于离线留痕。操作系统的断网隔离仍应由部署环境提供，本类本身不创建网络命名空间。

现有 `ExampleEvaluator` 的子进程启动也已抽成可注入的 `JsonlProgram`。若新模拟器能直接接受 `manifest.json` 并在任务目录生成符合 `SeriesPoint` 契约的 `result.tmp` JSONL，可给 `ExampleEvaluator(program=...)` 注入自己的实现；完整任意信号输出则使用上面的 `Simulator` 文件协议。

## 3. 串接模型候选

`ControlSystemIntegration` 组合 `TelemetryReader`、`RuleMonitor`、`Simulator` 和参数边界。模型输出的 `ControlOutput.candidate_pid` 与 `delta` 已可携带任意具名参数。`evaluate_candidate` 先检查观测质量和参数名称、绝对界、单次变化量、精度及重复候选，再将通过校验的参数交给离线模拟器；它没有设备写入方法。

```python
from packages.adapters.integration import ControlSystemIntegration
from packages.contracts.common import Bound, Constraints

integration = ControlSystemIntegration(
    reader, monitor, simulator,
    Constraints(parameter_bounds={
        "gain": Bound(min=0.0, max=3.0, max_delta=0.2),
        "ramp": Bound(min=0.0, max=10.0, max_delta=1.0),
    }, equality_tolerance=0.000001),
)
frame, status = await integration.check("pump-a")
# control_output 是模型返回并完成结构化校验的 ControlOutput。
trial = await integration.evaluate_candidate(frame, control_output, "steady-high")
```

现有 `/api/v1/triggers`、曲线页面与评分规则仍属于三参数压力 **EXAMPLE** 工作流。接真实系统前，需要确定厂商的读取协议、参数版本语义、信号单位、异常规则及模拟器目标函数，再把上述适配器接到新的运行配置与展示契约。不能把真实观测分数当作离线模拟器基线，也不能把模拟结果表述为现场已生效。
