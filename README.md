# 部门智能体平台（办公第一版）

当前版本新增本地账号、私有／公共 Skill 和智能体、17 项办公工具、附件与产物下载，以及对话式任务工作区。正式用户必须登录，`DEMO_LOCAL_DEV=1` 不再表示免登录。通用助手负责开箱即用的问答和写作，新对话统一进入“我的任务”；旧接待记录从任务页只读查阅，原 `#chat` 链接兼容。Monitor 和 B 回路保留原有流程，其业务记录及曲线按所有者隔离。

2026-10-19 的通用助手、办公工具扩充、资源权限与删除、选择面板调整已更新源码及本机预览，离线镜像、校验清单和更新盘待最终确认后统一重建。

内网安装、升级、初始管理员、验收与回退请阅读部署包根目录的 [office-v1.md](../office-v1.md)。初始管理员通过停服命令 `sudo bash deploy.sh init-admin` 创建；不得用第一个自行注册的用户替代初始化。

办公资源和发布版本保存在 SQLite，文件位于 `DEMO_STATE_DIR/office/`。自建 Skill 不再写入共享 `.opencode/skills/`；后端按任务加载数据库中的已发布版本，由固定 `office` Agent 和独立办公 MCP 工具执行。现有 `.opencode/` 继续保存受保护的工业业务配置。历史自定义技能首次导入为管理员私有草稿，原文件保留。

开发环境使用 Python 3.13.2，依赖固定在 `requirements.lock`；运行 `scripts/export_contracts.py` 后再生成前端类型。新增代码主要位于 `packages/adapters/office_*.py`、`packages/adapters/platform_store.py`、`apps/orchestrator/office*.py` 与前端 `office-*.tsx`。数据库迁移版本为 `0007`。

## ST 代码优化（2026-10-20 本机预览）

阶段纠错会携带上次未通过校验的完整结构化结果和具体错误，避免模型在丢失原测试输入时重新生成整组用例。扫描检查一次报告多个预期差异，不会直接改写模型断言；修复覆盖检查会列出遗漏的问题编号和建议。真实验收需从相同源码、操作及工程说明新建任务，检查 `retry_of=null`、`reused_stages=[]`，不能仅凭历史阶段续跑通过宣称新建流程已验证。使用 `scripts/smoke_st.py --source ...` 时默认保留空工程说明，`--instructions ""` 也不会再被示例工程约定覆盖。

登录后在侧栏“定制智能体 → ST 代码优化”使用，与 B 回路并列。可粘贴或上传 `.st`、`.scl`、`.txt` 纯文本（UTF-8 或 GB18030），选择通用 ST、CODESYS、Siemens SCL 或 TwinCAT 方言，并提供周期、单位、输入输出约定及厂商版本。每次最多 12000 字符、600 行；复杂工程应按 POU 分段提交。

四项操作可单独选择，按缺陷审查、添加注释、修复和测试分阶段执行，分别校验原生结构化对象：注释由平台按行插入，保留完整原文和有效词元，测试产物包含前提、步骤、预期及 ST 草稿，问题标明原文行号、证据和可信度，修复版由平台按原始行号应用最小补丁，仅修改有依据的已确认问题，保护 POU 名称和变量声明。测试程序的声明、BOOL 断言初始化、连续检查合并和汇总由平台生成；PROGRAM/FB 的模型只提交有界的扫描输入和字段预期，不允许生成自由 ST 前置代码、循环或调用串。每个用例由平台创建独立实例，每个扫描只调用一次；同一扫描的多个断言共用本次结果。测试说明与 ST 断言从同一份数据生成；对受限 ST 子集用零耗时逻辑模型核对扫描预期，算术/状态预期不符会拒绝并要求纠正，未支持语法明确标记未覆盖，不将它当成 PLC 编译或真实时间执行。测试程序只在首扫描执行，重测须重新初始化工程。函数草稿仍校验必要输入参数。PROGRAM 仅在测试草稿中封装为独立 ST_TestSubject 功能块，生产程序不变。依赖真实定时器/PLC 时钟的用例标为 manual，说明逐扫描条件与适配点，不计入 AllPassed。修复隐含问题审查；选择测试时每个修复问题必须关联回归用例。结果提供原文、注释版、测试、修复版、统一 diff、报告和 JSON 文件下载。

这是固定的 `st-code-v1` 程序流程，由 `.opencode/agents/st-code.md` 和后端共同维护，不是普通办公资源。Agent 不具备文件、Shell、办公或工业工具权限，只提交结构化结果。ST 阶段直接使用已配置模型服务的原生 JSON Schema 约束，不使用模型工具调用；不具备任何文件或执行工具。输入总行数和末行逐字核对，逐阶段保存已校验结果与请求编号。常见布尔边沿顺序错误和缺少结束分号由程序独立检查，跳过注释、字符串与编译指令；修复必须覆盖这些已证明的问题。后端检查产物结构、原文证据、注释词元、修复记录，以及 ST 结束符、测试变量声明、占位符和未定义辅助调用；**未运行 PLC 编译器和测试，不代表工程验证通过**。任务与源码保存在 SQLite `st_code_run` 表并按当前账号隔离。支持取消、幂等提交和持久化历史；失败、取消或中断后可点“重试原任务”，仅允许当前账号且输入完全一致。已保存阶段必须按当前规则重新校验才可复用，失效阶段及其后续阶段重新生成；重启时运行中的未知结果标记中断，不重发模型请求。整个任务默认最多 15 分钟，可用 `ST_MODEL_TIMEOUT_SECONDS` 在 60–1800 秒间配置；每个已完成但无效的阶段最多纠正一次，保留前面已通过的阶段，不重跑整个任务；界面显示当前阶段与校验记录。较长代码建议拆分或减少所选操作。

本机入口为 <http://127.0.0.1:18101/#st-code>。本轮只开发、构建本地镜像及更新预览，不制作镜像归档、离线更新盘或光盘刻录文件。真实模型验收可运行 `scripts/smoke_st.py --output /验收结果目录`，登录密码由交互输入或 `ST_SMOKE_PASSWORD` 提供。该命令会创建一条真实任务，测试草稿仍需在目标 PLC 环境适配执行。

下面保留原 PID Demo 的业务说明和历史部署记录；其中旧地址、共享身份和文件式技能注册描述不适用于办公第一版。

---

# OpenCode 接待与双业务 Agent PID 建议 Demo

当前 WSL 部署入口为 <http://100.64.0.17:18080>，模型仍运行于 server9 的 GPU 0、1。WSL 安装、服务和配置以 [WSL 部署说明](docs/runbooks/wsl-deployment.md) 为准；原服务器维护与回退见 [迁移协调文档](docs/runbooks/wsl-migration-coordination.md)。以下带 `/data9` 路径的说明描述原服务器部署。

本项目有两条独立流程：Monitor 读取持续运行的 `EXAMPLE` 压力回路并给出诊断；操作员可另行启动 `SIMULATION` 汽水循环 B，由真实 OpenCode Control Agent 提出给水泵 PID 候选，Python 编排器逐工况仿真、检查安全边界、评分并保存建议。**未连接真实设备，也没有参数下发接口。**

页面提供 Agent、Skill、Tool 三个目录中心。目录从项目的 `.opencode/agents/`、`.opencode/skills/`、MCP 工具定义及权限配置读取。Skill 中心可通过名称、描述、触发词和可选执行说明生成并注册 `SKILL.md`；模板中的 `name` 会预览实际生成的技能标识。自定义技能可经确认后移到 `.opencode/deleted-skills/`，并从「已删除」列表恢复。该列表还提供需再次确认的「彻底删除」，会永久移除归档技能目录及其中所有文件；三个工作流内置技能不可删除。注册、删除、恢复与彻底删除操作需要与其他写请求相同的身份、Origin、CSRF 和幂等键。新技能只写入项目目录，需单独在 Agent 权限配置中授权才可被调用。

接待对话顶部显示 [OpenCode 官方标识](https://github.com/anomalyco/opencode/blob/v1.18.31/packages/console/app/src/asset/logo-ornate-light.svg)，输入框底部显示后端当前配置的可用模型。模型名称取自运行配置，页面不会提供与实际服务不符的固定选项。

面向未来的内网系统，已提供[可插拔接入层与协议说明](docs/control-system-integrations.md)：具名参数集、原始字段映射、确定性多信号异常判定和本地文件式模拟器接口。当前 B 回路使用内置简化模型；接入真实系统需按现场字段与模型校准结果实现适配器。

开发与验收状态见 [development_progress.md](development_progress.md)。本机模型配置使用 GPU 0、1 上的 Qwen3.6 vLLM；模型、OpenCode 和后端只监听回环地址，tailnet 演示入口见下文。示例数据不代表真实设备，对外 HTTPS 仍未验收。

## 环境与安装

已核验 Python 3.13.2、Node 22.23.2、OpenCode 1.18.31。SQLite 数据必须放在支持 flock 的本地文件系统。只允许 **一个 Uvicorn worker，不使用 reload**。

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
npm ci --prefix apps/dashboard
.venv/bin/python scripts/export_contracts.py
npm run generate --prefix apps/dashboard
npm run build --prefix apps/dashboard
```

Python 锁文件固定已验证依赖；前端使用 `package-lock.json`。Schema/OpenAPI 和前端类型都是生成产物，修改 `packages/contracts` 后重新生成，不手写另一套契约。

## 启动与模型配置

首次生成项目专用服务凭据（文件存在则拒绝覆盖）：

```bash
.venv/bin/python scripts/init_runtime.py
```

`deploy/runtime.env.example` 是初始模板，其中的 OpenCode 免费模型在本项目自定义 Agent 调用中实测返回 403，不能用于端到端演示；详见 [兼容性记录](docs/runbooks/opencode-compatibility.md)。`OPENCODE_SERVER_PASSWORD` 和 `MCP_SERVICE_TOKEN` 是本地服务凭据，由上述脚本生成。

**模型配置改在 `deploy/runtime/runtime.env`，不是 `deploy/runtime.env.example`。**本机当前已切到独立 vLLM 服务，三项值为：

```dotenv
# deploy/runtime/runtime.env 中现有的非敏感配置
DEMO_PROVIDER=local-vllm
DEMO_MODEL=qwen3.6-35b-a3b
DEMO_PROVIDER_CONFIG=/data9-2/xjx/opencode-agent-demo/deploy/runtime/provider.vllm-gpu01.json
```

`DEMO_PROVIDER_CONFIG` 指向项目专用的 JSON 文件；本机文件由 `deploy/provider.vllm-gpu01.example.json` 复制，模型地址是 `http://127.0.0.1:8000/v1`。`DEMO_PROVIDER` 要与 JSON 的 `provider` 下的键一致，`DEMO_MODEL` 填 `models` 下的模型 ID。保留 `runtime.env` 中已经生成的 `OPENCODE_SERVER_PASSWORD` 和 `MCP_SERVICE_TOKEN`，不要重新生成或清空。

如需改用自定义模型服务，先创建单独的项目配置文件，并在副本中填写实际的 `baseURL` 和 `models`：

```bash
cp deploy/provider.example.json deploy/runtime/provider-custom.json
chmod 600 deploy/runtime/provider-custom.json
```

然后编辑 `deploy/runtime/runtime.env` 中的三个变量：`DEMO_PROVIDER` 填 JSON 的 `provider` 下的键（模板为 `project-llm`）；`DEMO_MODEL` 填 `models` 下的模型 ID；`DEMO_PROVIDER_CONFIG` 填该副本的**绝对路径**，例如 `/data9-2/xjx/opencode-agent-demo/deploy/runtime/provider-custom.json`。模板中的 `{env:DEMO_MODEL_API_KEY}` 从 `runtime.env` 中新增的 `DEMO_MODEL_API_KEY=...` 获取密钥，不要把密钥写进 JSON。修改这些配置后需要重启后端和 OpenCode，并重新运行真实模型探针与业务验收。模型地址/ID 必须来自实际可用服务。运行环境文件和私有 provider 文件都应为 0600，目录 0700，不进版本控制。不读取或覆盖 `/data9/xjx/.opencode` 的原配置和凭据。

### 本机 GPU 0、1 的 vLLM 试接

本项目的独立 vLLM 服务使用 GPU 0、1，监听 `http://127.0.0.1:8000/v1`，模型 ID 为 `qwen3.6-35b-a3b`。项目配置模板是 `deploy/provider.vllm-gpu01.example.json`；本机已有权限为 0600 的 `deploy/runtime/provider.vllm-gpu01.json`。如果服务已在 8000 运行，直接运行下方探针，确认模型 ID 和工具调用后沿用该服务；否则先确认 GPU 0、1 和端口空闲，再在**终端一**前台启动模型服务并保持运行：

```bash
cd /data9-2/xjx/opencode-agent-demo
bash scripts/launch_vllm_gpu01.sh
```

脚本使用本地模型 `/data9/HF-model/Qwen3.6-35B-A3B` 和 `/data9/whp/.conda/envs/openhands-py2c/bin/vllm`，只监听回环地址；检测到端口或 GPU 被占用会退出，不会停止现有服务。可在命令前设置 `VLLM_BIN=/其他环境/bin/vllm` 切换环境。脚本会将该环境的 `bin` 和 `lib` 加入搜索路径，检查 FlashInfer 所需的 `ninja`，并开启工具解析与非思考模式。首次加载权重和编译可能需要十多分钟；本机首次加载约 15 分钟。

模型服务显示启动完成后，在另一终端运行独立探针：

```bash
cd /data9-2/xjx/opencode-agent-demo
.venv/bin/python scripts/probe_vllm.py
```

探针只在 `ready_for_agents: true` 时成功退出，分别验证普通文本、JSON Schema、自动工具调用和指定工具调用。独立服务按 [Qwen3.6 官方模型卡](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)启用了工具解析。该探针只验证 vLLM API；还须按下文启动后端与 OpenCode，运行完整模型探测和业务验收。修改磁盘上的 `runtime.env` 不会热更新已运行服务。

`doctor.py` **只读取执行它的终端环境变量，不会自动读取 `runtime.env`**。在另一个终端检查时，也要先加载环境；否则即使后端和 OpenCode 已正确加载配置，`configuration` 仍会显示 `false`：

```bash
cd /data9-2/xjx/opencode-agent-demo
set -a
source deploy/runtime/runtime.env
set +a
.venv/bin/python scripts/doctor.py
```

`configuration` 中三个配置项为 `true` 只表示变量已加载，不证明模型调用成功。`port:14096` 和 `port:18000` 表示当时的服务监听状态；`systemd: degraded` 是整个用户 systemd 管理器的状态，其他用户服务失败也会触发，与模型是否就绪无直接关系。需要实际调用模型时，在 vLLM、后端和 OpenCode 都启动后运行 `.venv/bin/python scripts/doctor.py --model-probe`。

运行文件使用 systemd EnvironmentFile 语法；若用于下面的 shell 命令，变量值也需符合 shell 赋值语法，含空格或特殊字符的值须妥善引用。

### 手动启动（各进程独立）

先确认 OpenCode 为项目要求的 **1.18.31**。启动脚本和后端健康检查都会拒绝其他版本；下面的命令仅在版本不符时切换版本：

```bash
if [ "$(/data9/xjx/.opencode/bin/opencode --version)" != "1.18.31" ]; then
  /data9/xjx/.opencode/bin/opencode upgrade 1.18.31 --method curl
fi
/data9/xjx/.opencode/bin/opencode --version
```

终端一按上文启动或检查 vLLM；等模型探针通过，再分别启动后端和 OpenCode。手动启动某个进程前，先停止对应的 systemd unit，避免端口或运行锁冲突；其他独立服务可继续运行。确认 14096 和 18000 没有被另一份项目服务占用；如果当前本项目服务已经运行且健康检查通过，可直接使用，改配置或代码时须先重启对应服务。

```bash
for unit in 719agent-demo-backend.service 719agent-demo-opencode.service; do
  if systemctl --user is-active --quiet "$unit"; then
    systemctl --user stop "$unit"
  fi
done
ss -ltnp '( sport = :14096 or sport = :18000 )'
```

**终端二：启动后端**，保持该终端运行。以下命令都在项目根目录执行：

```bash
cd /data9-2/xjx/opencode-agent-demo
set -a
source deploy/runtime/runtime.env
set +a
.venv/bin/python -m uvicorn apps.orchestrator.main:app --host 127.0.0.1 --port 18000 --workers 1 --no-proxy-headers
```

**终端三：启动 OpenCode**。打开新的终端，重新进入项目目录并加载同一份环境文件；等终端二的后端启动后再执行：

```bash
cd /data9-2/xjx/opencode-agent-demo
set -a
source deploy/runtime/runtime.env
set +a
.venv/bin/python scripts/launch_opencode.py
```

**独立终端：启动示例回路**。它与后端、OpenCode 各自启停；只需使用与后端相同的 `DEMO_STATE_DIR`。首次启动后需积累 120 秒采样，检查才会就绪。

```bash
cd /data9-2/xjx/opencode-agent-demo
set -a
source deploy/runtime/runtime.env
set +a
.venv/bin/python -m examples.live_process --state-dir "$DEMO_STATE_DIR"
```

页面：<http://127.0.0.1:18000> 或 <http://localhost:18000>。终端一的 vLLM 监听 8000，终端二的后端监听 18000，终端三的 OpenCode 监听 14096。后端每 60 秒执行依赖探测；模型未配置或不可用时显示 degraded，触发返回 503。OpenCode 的 `/global/health` 成功不等于模型就绪。**服务不提供 Fake 模式**，测试只通过构造注入使用 Fake。

### Tailnet 内演示

本机 tailnet 地址为 `100.64.0.6` 时，页面入口是 <http://100.64.0.6:18080/>。同一 tailnet 中、获准访问这台机器的设备可用浏览器打开；访问权限由 tailnet 成员与 ACL 控制。此入口使用 HTTP，流量由 Tailscale 隧道加密，仅用于私有 tailnet 演示，不作为公网发布方式。

`719agent-demo-tailnet.service` 只绑定 `100.64.0.6:18080`，仅提供页面资源与 `/api/v1/`。后端、OpenCode 和 vLLM 仍监听回环地址；`/internal/`、`/live` 不对 tailnet 开放。首次启用时将 `deploy/runtime/runtime.env` 的 `DEMO_ORIGIN` 设为 `http://100.64.0.6:18080` 并重启后端，以便浏览器写请求通过 Origin/CSRF 校验。本机页面 `http://127.0.0.1:18000` 仍可操作。当前入口不提供单独登录，tailnet 内所有获准访问此设备的成员共用演示身份和数据。

```bash
systemctl --user enable --now "$PWD/deploy/719agent-demo-backend.service"
systemctl --user enable --now "$PWD/deploy/719agent-demo-tailnet.service"
curl --noproxy '*' http://100.64.0.6:18080/api/v1/health
```

检查工作流还要求独立示例回路正在运行，且对应工况已有连续 120 秒采样；可按“常驻服务与日志”小节单独启动回路。

若 tailnet IP 变化，更新 `deploy/719agent-demo-tailnet.service` 的 `--host` 和 `deploy/runtime/runtime.env` 的 `DEMO_ORIGIN`，然后运行 `systemctl --user daemon-reload` 并重启两个服务。停止共享：`systemctl --user disable --now 719agent-demo-tailnet.service`。

模型、后端、OpenCode 和示例回路都启动，且回路已积累连续 120 秒样本后，在已加载环境的终端运行：

```bash
.venv/bin/python scripts/doctor.py --model-probe
.venv/bin/python scripts/smoke_real.py
```

`doctor.py` 的 `real_model_probe.ready: true` 表示 Monitor 的结构化就绪探测成功。`smoke_real.py` 应报告证据文件已写入；它会检查 Monitor 对 `normal` 和 `improvable` 的诊断、异常后不自动调用 Control，以及独立 B 回路中真实 Control Agent 候选的四工况评测。页面位于 <http://127.0.0.1:18000>。手动启动的各进程可分别在各自终端按 `Ctrl-C`，停止后端不会停止示例回路。改用 systemd 常驻方式前，停止对应的手动进程，避免运行锁冲突。

`launch_opencode.py` 在 `var/opencode` 使用隔离 XDG 目录，固定版本、Basic Auth、回环 14096、默认拒绝权限和项目三个 Agent（接待、Monitor、Control）。每次推理独立 Session，仅发送一条业务用户消息；客户端由 Session ID 生成固定 messageID，通过 parentID、Agent、完成字段关联结果。OpenCode 1.18.31 读取完整结构化消息列表会返回 HTTP 400；客户端只读取最新一条 Assistant 消息规避该版本问题。

## 演示操作

### 持续运行的示例回路

独立的示例回路进程在 `DEMO_STATE_DIR/telemetry/` 为 `normal`、`improvable`、`coupled_load`、`no_improvement` 四个虚拟工况创建 JSONL 文件，并每秒追加一条状态。后端只读这些文件，不启动或停止回路。首次启动及较长停机后的缺口**不回填**；只有最近 120 秒内有 121 个连续的 `LIVE` 样本时，后端才接受 Monitor 检查。Monitor 冻结窗口并诊断；异常时提示操作员另行发起 B 回路调优，不会自动调用 Control。

```bash
# 已加载 runtime.env，且后端在运行
curl --noproxy '*' http://127.0.0.1:18000/api/v1/telemetry/improvable/latest
tail -n 3 "${DEMO_STATE_DIR:-var/backend}/telemetry/improvable.jsonl"
```

`running` 表示独立回路进程持有运行锁；`window_ready` 表示该工况已有可诊断的连续 120 秒窗口。停止回路时新的 Monitor 检查会返回 503；B 回路是独立的离线仿真，不依赖该采样器。旧压力曲线与旧调参结果仍可查询，其 `EXAMPLE` 分数不得与 B 回路的 `SIMULATION` 分数比较。

主页以接待 Agent 对话为默认入口。明确的检查请求创建 Monitor 诊断；“B 回路仿真调优”标签页提供独立的启动按钮、运行历史与逐工况曲线。两条流程都不具备下发权限。

1. 页面确认依赖就绪，选择 `normal`：Monitor 与规则一致时无需调参。
2. 选择 `improvable`：Monitor 报告异常并提示人工发起 B 回路调优；不会自行调参。
3. 切换“B 回路仿真调优”，启动一次离线运行，查看四种负荷变化的事件、基线与候选响应及最终建议。
4. 可以取消调优；任一候选若在某个工况越过仿真安全界，整组参数即被淘汰。

Monitor 定时检查默认关闭。B 回路最多评测 12 组 Control 候选，连续 4 轮无改善或 20 分钟预算到期后结束；同一时刻只允许一条 B 调优运行。

### B 回路模型与接口

B 回路名义点为汽轮机 500 kW、蒸汽 3.26 t/h、锅炉热输入约 2.5 MW、主蒸汽压力 3.8 MPa。模型包含汽包水/蒸汽库存、汽轮机流量与功率、凝汽回水、泵速与给水流量；燃烧调节和阀门负荷前馈策略固定。泵 PID 采用并联式，以额定给水流量归一化误差控制泵速，含输出限幅和积分抗饱和。模型与事件、边界及评分版本保存在每次运行记录中。它是**未经现场校准的简化仿真模型**，建议不能直接应用到回路 A。

负荷工况固定为：60 s 升至 550 kW、60 s 降至 450 kW、60–180 s 斜坡升至 600 kW，以及 60/180/300 s 多次升降。每组 PID 都从相同名义状态完成四个独立试验。每个工况先检查水位 0.3–0.7、主蒸汽压力 3.42–4.18 MPa、给水流量 0–1.45 kg/s 和泵速 0.35–1.0 的演示边界。安全通过后，按流量 40%、水位 20%、压力 20%、功率 10%、泵速变化 10% 计算单工况分；总分为平均分 70% 加最差工况分 30%，越低越好。

`POST /api/v1/tuning-runs` 启动，`GET /api/v1/tuning-runs` 列表，`GET /api/v1/tuning-runs/{run_id}` 详情，`POST /api/v1/tuning-runs/{run_id}/cancel` 取消。详情中的 trial/scenario ID 可用于 `GET /api/v1/tuning-runs/{run_id}/trials/{trial_id}/scenarios/{scenario_id}/trace` 读取 1 s 采样时序。写入 API 使用与原有流程相同的身份、CSRF 和幂等键。

模型 `steam-cycle-b-v2` 支持给水泵、给水调节阀、锅炉、汽轮机和凝汽器的独立老化。页面“创建回路状态”提供 0～100% 滑块、标称/轻度/中度/重度预设（0%/25%/50%/85%）、随机生成（各部件独立取 0～70%）及历史状态复用。配置完成后点击“开始仿真调优”。老化影响扬程、通流能力、响应时间、热效率和循环比功；具体系数保存在运行 `assumptions.aging_effects` 与 `effective_parameters` 中。百分比是模型强度，不代表服役年限或失效概率。

启动请求可携带以下状态；省略时保持全新部件的原有行为。每次运行冻结并保存 `plant_state` 和包含模型版本的 `plant_state_hash`，基线及所有候选均使用该状态；Agent 获得相同的老化配置和有效模型参数，只能调整 PID。相同状态可重复评测，不同状态之间不直接比较分数。各试验保持相同初始水汽库存、泵速、流量和热输入；老化时初始点不保证平衡，负荷事件前的漂移也计分。严重老化可能无法仅靠 PID 消除安全越界，安全边界不随老化放宽。旧记录缺少状态字段时按全零老化读取，无需数据库迁移。

```json
{
  "schema_version": "1.0",
  "plant_state": {
    "name": "检修前的 B 回路",
    "aging": {
      "feedwater_pump": 0.6,
      "feedwater_valve": 0.3,
      "boiler": 0.4,
      "turbine": 0.2,
      "condenser": 0.5
    }
  }
}
```

老化值必须是 0～1 的有限数字，拒绝布尔值、数值字符串和未知部件名。`simulate(pid, scenario_id, plant_state)` 与 `evaluate_pid(pid, plant_state)` 也支持直接传入 `PlantState`，用于离线批量创建和评测不同回路状态。

Control 的 `search_feedback` 明确总分越低越好，并提供相对基线的综合改善、模型中不受泵 PID 影响的分量，以及尚未试验的有界单参数方向。当前 v1/v2 简化模型的压力、功率子系统没有给水流量反馈，因此不能靠调泵 PID 降低其分量；它们仍参与总分，压力安全界也照常检查。初期建议先评测 3 个候选，以单参数探测辨识方向，而不要求模型事先保证每次改善或所有分量同时下降。

程序对探索初期已确认的 `NO_CANDIDATE` 最多安排一次同轮复核，原理由记录在 `search_reviews` 并审计。复核使用新会话与授权上下文，但与流程纠错共享每轮最多 3 次模型请求的上限，不增加仿真轮次或连续未改善计数。再次放弃、不可控压力已越界、预算到期或取消时正常停止；程序不自行生成候选代替 Agent，也不强迫跑满 12 轮。已有终态运行保持原样，可复用状态新建运行。

## 检查与验收

以下命令也应在项目根目录、已加载 `deploy/runtime/runtime.env` 的终端执行；`--model-probe` 和 `smoke_real.py` 会实际调用所配置的模型。

```bash
bash scripts/check.sh
.venv/bin/python scripts/doctor.py
.venv/bin/python scripts/doctor.py --model-probe
.venv/bin/python scripts/probe_opencode.py
.venv/bin/python scripts/probe_agents_mcp.py
.venv/bin/python scripts/smoke_real.py
```

- `check.sh`：确定性、契约、API、恢复、权限和备份测试，Ruff，前端生成/typecheck/build。
- `probe_opencode.py`：临时隔离实例及实际 `/doc`，不使用模型。
- `probe_agents_mcp.py`：实际 Agent 权限配置及 MCP 连接/四工具/拒绝无效凭据；需要回环后端，仍不替代模型工具调用验收。
- `smoke_real.py`：后端 ready 后才运行，检查 Monitor 诊断、与 Control 解耦及独立 B 回路真实 Agent 候选评测。失败不写成功记录。实际模型超时/abort 探针仍需按兼容性记录补齐。

一次隔离环境中的真实 Control Agent 提案及四工况裁决结果见 [B 回路验收记录](docs/runbooks/simulation-b-acceptance.md)。
- 浏览器验收见 `apps/dashboard/tests`；测试专用后端模块明确使用 Fake，不是部署入口。两个用例分别检查 18001 的测试链路和 18000 的真实服务页面，后者的就绪显示须与健康接口一致。

```bash
npm exec --prefix apps/dashboard -- playwright install --no-shell chromium
npm run test:browser --prefix apps/dashboard
```

Playwright 使用完整 Chromium 的 headless 模式，测试会自动启动/停止 18001 的独立后端，数据写入 `var/browser-tests`。不与正式 `var/backend` 混用。

## 常驻服务与日志

仓库提供本机绝对路径的 user units；换机器先修改路径。vLLM 须先按上文单独启动并通过探针。示例回路、后端和 OpenCode 是三个独立的 user services，没有互相启动或停止的依赖。示例回路 unit 的 `--state-dir` 必须与后端 `DEMO_STATE_DIR` 一致。以下命令在项目根目录执行；新回路服务先链接，再按需启动，不默认设为开机自启。

```bash
cd /data9-2/xjx/opencode-agent-demo
systemctl --user link "$PWD/deploy/719agent-demo-loop.service"
systemctl --user start 719agent-demo-loop.service
systemctl --user enable --now "$PWD/deploy/719agent-demo-backend.service"
systemctl --user enable --now "$PWD/deploy/719agent-demo-opencode.service"
```

可先单独启动回路，让它在后端关闭时继续采样；回路积累 120 秒后，再启动后端进行检查。需要回路随 user manager 自动启动时，可单独执行 `systemctl --user enable 719agent-demo-loop.service`。

查看状态和日志：

```bash
systemctl --user status 719agent-demo-loop.service 719agent-demo-backend.service 719agent-demo-opencode.service
journalctl --user -u 719agent-demo-loop.service -n 100 --no-pager
journalctl --user -u 719agent-demo-backend.service -n 100 --no-pager
```

停止服务：

```bash
systemctl --user stop 719agent-demo-loop.service       # 只停采样
systemctl --user stop 719agent-demo-backend.service    # 只停后端
systemctl --user stop 719agent-demo-opencode.service   # 只停模型编排服务
```

如果还要取消后续自动启动：

```bash
systemctl --user disable 719agent-demo-loop.service 719agent-demo-backend.service 719agent-demo-opencode.service
```

本机 `Linger=yes`，user manager 可脱离终端运行。`KillMode=control-group` 管理示例子进程；开发方式 Ctrl-C 会清理子进程。后端和回路各持有独立运行锁；不要同时手动和 systemd 启动两份后端或两份回路。

对外发布前，使用 `deploy/nginx.conf.example` 填入真实域名、证书、账户、允许网段和代理凭据；`DEMO_LOCAL_DEV=0`、`DEMO_ORIGIN=https://实际域名`。Nginx 只提供页面/API，拒绝 `/internal/` 和 `/live`，覆盖身份头。Basic Auth + 可信代理身份 + Origin/CSRF 均需验收，禁止直接暴露 14096/18000。当前未安装或修改系统 Nginx。

## 备份与恢复

备份前需分别停止后端和示例回路，以取得两个进程锁，保证 DB、曲线和时序 JSONL 一致；不用直接复制一个运行中的 SQLite 文件。

```bash
systemctl --user stop 719agent-demo-backend.service
systemctl --user stop 719agent-demo-loop.service
.venv/bin/python scripts/backup.py backup var/backend /安全目录/demo-backup-日期
.venv/bin/python scripts/backup.py restore /安全目录/demo-backup-日期 var/restored
# 恢复工具拒绝覆盖已有目标，并校验所有文件与数据库记录哈希。
# 将 runtime.env 中 DEMO_STATE_DIR 和回路 unit 的 --state-dir
# 都改为 var/restored 的绝对路径，执行 systemctl --user daemon-reload，再启动：
systemctl --user start 719agent-demo-backend.service
systemctl --user start 719agent-demo-loop.service
```

备份包含 DB、曲线、时序 JSONL、job manifest/receipt，不包含 provider 配置、服务凭据或 OpenCode Session 数据。恢复中的旧推理若无法唯一找回结果则转人工；Token 在进程重启后全部失效。完整结果文件可按 receipt 回收，最多一次确定性重算；无法证明旧子进程停止则不重跑。

## Adapter 扩展

`packages/adapters/interfaces.py` 定义 `ObservationSource.read_snapshot/health` 与 `CandidateEvaluator.submit/get/cancel`。观测与实际 PID 必须来自同一冻结快照；评测接口只产生曲线，不能接受候选或真实写入。返回值使用 `packages/contracts`，未知状态不能伪装成 NOT_FOUND。`tests/test_adapter_contract.py` 提供第二组极简测试实现。

未来真实接入需单独确定单位、PID 形式、工况和评测有效性，并建设审批与安全网关。本 Demo 不能通过切换 source_kind 获得真实写入能力。
