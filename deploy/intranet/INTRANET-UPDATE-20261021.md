# 2026-10-21 内网累计更新：刻盘与现场逐步手册

本手册适用于已经部署成功、已经解决 403 和 Chrome 86 不兼容的那台 Linux x86_64 服务器。**本次只用一张更新 DVD；不需要按顺序安装任何中间版本。** 原模型服务、模型权重、GPU 驱动和现场配置继续使用。新应用镜像标签为 `opencode-agent-demo:20261021-intranet-amd64`。

本次包含：既有内网/Chrome 86 修复、MCP 动态端口、离线 ripgrep、账号与私有/公共资源、17 项办公工具、通用助手、附件和产物、B 回路技能/上下文/搜索/老化/时间轴改进，以及 ST 注释/审查/修复/扫描周期验证与最近的重试修复。当前数据库最新版本为 `0007`，初始化/启动时自动迁移，**不要手工修改版本号**。升级前共享历史归初始管理员，普通成员需注册登录。

本次不是模型升级；不复制制作机的账号、运行数据、配置密钥或测试模型。不要把整个开发目录直接刻盘。仅复制下面指定的交付目录。

## 1. 在家刻什么，刻到哪张盘

唯一交付目录：`delivery/intranet-20261021/disc-01/`。把它**里面的全部内容**刻到一张 4.7 GB 数据 DVD 的根目录，卷标建议 `INTRANET_20261021`。不要刻成音频/视频盘；不要刻旧版 `office-v1` / `b-loop-*` 目录。无需第二张内容盘；如担心介质读错，可另刻一张完全相同的备用副本，标记“备份盘，内容相同”，不按两盘依次安装。

| 光盘根目录 | 用途 | 现场放置方式 |
| --- | --- | --- |
| `START-HERE.txt`、本手册 | 从这里开始 | 放到硬盘收件目录，随时查阅 |
| `DISC-SHA256SUMS`、`MANAGED-PATHS.txt` | 运输校验和替换范围 | 保持原样，不编辑 |
| `scripts/intranet-update.sh` | 预检查、停服备份、应用、回退 | 从收件目录运行 |
| `payload/images/` | 最新应用 + 完整办公依赖镜像 | 更新脚本导入 Docker，并放入原部署目录 |
| `payload/source/`、`build/`、`runtime/`、`scripts/` 等 | 与镜像对应的源码和离线重建材料 | 更新脚本替换原部署目录的对应项 |
| `payload/RELEASE-SHA256SUMS`、`verification/` | 当前版本校验和测试证据 | 随更新安装 |

不另带 vLLM 大镜像、模型权重或 Docker 安装盘；本盘保留 Docker CLI 压缩包供原机没有 `docker` 命令时使用。最终实际字节数、文件数、镜像 ID 见盘内 `RELEASE-INFO.json`，容量由制作程序强制限制在 4,000,000,000 字节以内。

刻录建议：选择数据光盘、UDF 或支持长文件名的格式，结束后封盘并开启刻录软件的读回校验。保留目录层次、中文文件名和以点开头的文件。每个文件都小于 FAT32 的单文件上限，但建议直接复制整盘，避免手工挑文件。

**刻完后必须把实体盘重新读回校验。** Linux 可在挂载目录执行 `sha256sum -c DISC-SHA256SUMS --quiet`；无输出且退出码为 0 表示通过。Windows 刻录软件的“验证数据”也应打开；Linux 上完整校验仍是部署前的必做项。不要用压缩软件“智能解压/合并同名目录”改变内容。

问题处理：容量不足说明选错目录或不是 DVD；只刻 `disc-01` 内的内容。读回不通过、某文件缺失或读盘有 I/O error 时重刻/换盘，不修改清单来让校验通过。若光盘外面多了一层 `disc-01`，现场把 MEDIA 指向这层即可。

## 2. 到所里先定位原服务，不停机

以下在 **Linux 服务器终端**操作；不是 Windows CMD/PowerShell。使用原来的 sudo 账号。当前已知网页地址 `http://10.70.100.220:3000`，OpenCode 回环端口 `14097`，vLLM `http://127.0.0.1:4625/v1`。以原机已验证配置为准，不用制作机配置覆盖。

先从之前部署时的目录进入。若不确定目录，可用系统 Docker 查询：

```bash
sudo docker inspect -f '{{index .Config.Labels "io.opencode-demo.bundle"}}' opencode-agent-demo
```

如果 `docker: command not found`，先找到原部署目录，用其中 `.tools/docker/docker` 替代 `docker`；或在下面复制盘后使用随盘 CLI：

```bash
mkdir -p "$HOME/intranet-docker-cli-20261021"
tar -xzf "$HOME/intranet-update-20261021/payload/runtime/docker-28.5.2-linux-x86_64.tgz" -C "$HOME/intranet-docker-cli-20261021"
sudo "$HOME/intranet-docker-cli-20261021/docker/docker" inspect -f '{{index .Config.Labels "io.opencode-demo.bundle"}}' opencode-agent-demo
```

记录输出的绝对路径。下面例子 `/opt/opencode-agent-demo-deploy` **必须替换为实际输出**；不要新建一个同名部署目录来替代原目录。

```bash
TARGET=/opt/opencode-agent-demo-deploy
cd "$TARGET"
sudo bash deploy.sh status
sudo bash deploy.sh check --wait 300
```

预期：旧服务能运行、旧网页正常，模型连通。记录当前失败项；若升级前就模型不可达，先恢复模型，避免混淆新旧问题。没有容器、标签为空、目录不符时停止升级，确认实际服务所在的主机/目录/Docker daemon；不要强删同名容器。若原机使用非默认 `DOCKER_HOST`，后续每个 sudo 命令均显式携带原 socket，例如 `sudo env DOCKER_HOST=unix:///实际/docker.sock bash ...`，不要指向本机制作环境的 socket。

## 3. 光盘复制到服务器硬盘收件目录

用 `lsblk -f` 或 `findmnt` 确认挂载点。桌面自动挂载时常在 `/media/用户名/卷标`。无自动挂载可执行：

```bash
sudo mkdir -p /mnt/intranet-dvd
sudo mount -o ro /dev/sr0 /mnt/intranet-dvd
```

如果提示无介质/特殊设备不存在，检查光驱设备名和介质；不要格式化设备。若已挂载，直接用已有挂载点。

```bash
MEDIA=/mnt/intranet-dvd
STAGE="$HOME/intranet-update-20261021"
mkdir "$STAGE"
cp -R "$MEDIA/." "$STAGE/"
chmod -R u+rwX "$STAGE"
cd "$STAGE"
sha256sum -c DISC-SHA256SUMS --quiet
bash scripts/intranet-update.sh verify
```

如果是 Windows 中转：把盘内全部内容复制到一个普通目录，再用所内批准的文件传输方式完整传到上述 STAGE。路径不要放在原部署目录内，脚本会拒绝相互嵌套。原有同名收件目录存在时，先改名保留，重新复制到空目录；不要把本次盘和旧盘混合。

预期两次校验成功。任何 `FAILED` / `No such file` / `unexpected EOF` 都应停止：完整重复制或换备用盘；不要继续 load/apply。`Permission denied` 通常是从只读盘运行/复制到无权限目录，使用上述家目录并调用 `bash 脚本名`。`^M`/`$'\r'` 表示传输工具转换了脚本换行，重新按二进制传输后校验，不在校验过的介质上手工修补。

## 4. 升级前预检查，明确磁盘空间和停机范围

若换了终端，重新设置 TARGET、STAGE。之后即使 cd 切换目录，变量仍有效。

```bash
TARGET=/opt/opencode-agent-demo-deploy
STAGE="$HOME/intranet-update-20261021"
sudo bash "$STAGE/scripts/intranet-update.sh" check "$TARGET"
df -h "$TARGET" /var/lib
sudo du -sh "$TARGET/data/var" "$TARGET/data/catalog"
```

预检查验证：整盘哈希、Linux/Docker 架构、原容器归属、host 网络、两个数据挂载、现场配置换行、符号链接和部署分区空间。它不会停止服务。脚本给出的 DockerRootDir 若在另一磁盘，对那个路径再执行 `df -h`，至少留 **3 GiB**；部署分区按脚本估算留出数据/代码备份及失败现场空间，建议在其要求之外再留余量。

空间不足：选择本地大容量 ext4/XFS 分区，或由管理员释放明确可再生成的缓存；不要删除 `data/`、业务备份、原镜像或 GPU 模型。不要在 NFS/CIFS/光盘上放数据库。符号链接/非标准挂载被拒绝时不要绕过检查复制，先按提示确认真实路径和挂载结构。

预留停机窗口：确认无人正在执行办公任务、Monitor、B 回路或 ST 工作流。建议准备 30–60 分钟用于备份/升级/验收；时间取决于数据量和模型速度，ST 完整任务可能单独需要更久。正在执行的任务在重启后可能标记中断，需要检查已有结果再重试。

## 5. 一条命令完成旧镜像保存、停服备份和累计更新

```bash
sudo bash "$STAGE/scripts/intranet-update.sh" apply "$TARGET"
```

脚本依次执行：重新校验 → 保存当前容器实际镜像 → 停服 → 备份现场配置、`data/var`、`data/catalog` → 备份将被替换的旧文件 → 校验备份 → 导入新镜像 → 替换完整源码目录及配套文件 → 校验安装文件。它不会覆盖现场 `config/demo.env`，也不会自动启动。

**记下输出的回退目录**，形如 `/原部署目录/backups/intranet-20261021-AbCd1234`。它含旧代码、旧镜像、升级前数据库和配置，不能刻回公共盘或删除。`replaced/` 是被替换的原文件，供进一步人工恢复。`BACKUP-COMPLETE` 表示具备完整回退材料；`APPLY-COMPLETE` 表示安装完成。

预期最后显示“更新文件已安装，服务仍停止”。导入镜像/压缩备份期间可能数分钟无输出，等命令结束，不强制断电。若连接中断，重新登录后先检查脚本进程与上述标记；不要同时再开一遍 apply。

问题处理紧随操作：

- 保存镜像失败且尚未显示“服务已停止”：旧服务未修改，检查磁盘或 Docker 后再执行。
- 已停服、但没有 `BACKUP-COMPLETE`：脚本尚未替换代码/数据；解决空间/权限错误后可以从原目录 `sudo bash deploy.sh up` 恢复旧服务。保留未完成备份以便诊断，稍后重新 apply。
- 已有 `BACKUP-COMPLETE`、但没有 `APPLY-COMPLETE`：可能只导入镜像或替换了一部分文件，**不要启动混合版本**；直接按第 10 节回退，然后排除空间/复制错误再重做。
- `load` 后仍是旧页面：load 只导入，尚未 up；应继续第 6–7 节，不需要再导入旧盘。
- 完成后不要重复 apply。本次第二次部署应从回退恢复的旧状态开始；脚本拒绝对已经安装此版本的目录重复应用。

## 6. 保留原配置，首次创建管理员

```bash
cd "$TARGET"
sudo bash deploy.sh verify
sudo grep -E '^(APP_HOST|APP_PORT|DEMO_ORIGIN|DEMO_ALLOWED_CIDRS|OPENCODE_PORT|VLLM_BASE_URL|VLLM_MODEL|VLLM_CONTEXT|VLLM_MAX_OUTPUT)=' config/demo.env
```

该命令不打印 API key。对于当前现场，应确认：

```text
APP_HOST=0.0.0.0
APP_PORT=3000
DEMO_ORIGIN=http://10.70.100.220:3000
DEMO_ALLOWED_CIDRS=10.70.0.0/17
OPENCODE_PORT=14097
VLLM_BASE_URL=http://127.0.0.1:4625/v1
```

模型 ID、密钥、上下文预算保留原机验证值；不要照抄别处模型名。`VLLM_CONTEXT` 必须不超过模型真实窗口，输出预算必须小于上下文。新增办公限制有代码默认值，无须为了升级重写配置。若要修改用 `sudo nano config/demo.env`（无 nano 用 vi），使用 `KEY=value`、不加引号/export/行尾注释。配置含 CRLF 时先备份再 `sudo sed -i 's/\r$//' config/demo.env`。每个键只写一次。

```bash
sudo bash deploy.sh models
sudo bash deploy.sh probe-model
sudo bash deploy.sh init-admin
```

`models` 应列出原模型；`probe-model` 应 `ready_for_agents: true`，验证文本、结构化输出及自动/指定工具调用。若失败：连接拒绝→确认原 vLLM 已加载并监听 4625；401→核对原 API key；model not found→逐字匹配 models 的 id；工具/JSON失败→恢复原已验证 vLLM 启动参数及解析器，不要在断网机临时 pip/npm install。

`init-admin` 在服务停止状态运行，会迁移数据库并交互询问账号和两次密码（至少 10 位，输入不回显属正常）。它把原共享业务历史归此管理员。请保管账号密码，不写进命令行/文档。`两次密码不一致` 可重试；`初始管理员已存在` 表示此前已初始化，使用原账号，不重复重置；忘记密码时使用现有管理员的账号管理/原备份，不删除数据库。`database locked` 表示还有后端或重复容器占用，先确认原服务已停；不要删除 lock/db 文件绕过。迁移失败保留报错并回退整套版本，不能只还原数据库版本表。

## 7. 启动与服务器验收

```bash
cd "$TARGET"
sudo bash deploy.sh up
sudo bash deploy.sh status
sudo bash deploy.sh check --wait 300
```

`up` 重建容器，保留挂载数据，并在启动前检查端口和配置。连续采样需约 120 秒，磁盘繁忙时更久；最终应输出 `ready: true` / 验收通过。`healthy` 只表示进程和本地 API 存活，不等于完整业务就绪。

如需确认镜像（有系统 Docker 时）：

```bash
sudo docker inspect -f 'tag={{.Config.Image}} id={{.Image}} state={{.State.Status}}' opencode-agent-demo
```

应为新标签，ID 与盘内 RELEASE-INFO 一致；没有系统 Docker 时使用第 2 节的 CLI。`up` 若报端口占用，用 `sudo ss -ltnp | grep -E ':(3000|14097)\b'` 定位进程；不要杀其他业务服务。更换网页端口时同步修改 APP_PORT 与 DEMO_ORIGIN；更换 OPENCODE_PORT 时保持回环限制。启动报 Illegal instruction 属 CPU 与二进制兼容问题，保留旧版并回退，不能靠放开网络解决。

`check` 超时按以下顺序排查，每修复一项再运行一次 check：

```bash
sudo bash deploy.sh logs
sudo bash deploy.sh probe-model
sudo bash deploy.sh probe-agent
sudo bash deploy.sh check --wait 300
```

模型未就绪→修复 vLLM；MCP 未就绪→查看 OpenCode/内部端口和日志，当前代码已统一读取 `INTERNAL_API_BASE_URL`；TELEMETRY_WINDOW_NOT_READY→等磁盘大操作结束后再积累两分钟连续采样，不伪造/回填样本。日志里出现 SQLite readonly/permission denied 时确认 sudo、本地文件系统和挂载，不对 data 执行 chmod 777；SELinux 拒绝由管理员核对容器挂载标签（脚本已有 `:Z`）。

## 8. 在真实内网 Windows / 360 Chrome 86 上验收

用原来的客户端，360 选择**极速/Chromium 内核**，直接打开 `http://10.70.100.220:3000`，先按 Ctrl+F5。不要用兼容/IE 模式，不要把来源改为随意的代理地址。

逐项完成：

1. 显示登录页，用管理员登录。新成员注册登录；退出后重新登录一次。
2. 智能体中心打开通用助手，发一句含 Markdown 的请求，继续第二轮，刷新后历史仍在。
3. 办公工具打开“数值与统计计算”，输入 `mean([2,4,6]) + 1`，结果为 5。
4. “生成 Word”填标题和两段正文，下载并在 Word 中打开，确认正文存在（不能只看到标题）。再上传小型 Excel/文本做一次读取；检查中文文件名。
5. 创建一个私有 Skill 和智能体并发布，用另一个成员账号确认不可见；如需共享，按界面设置公开。确认旧业务历史只归初始管理员。
6. 切换 Monitor、B 回路、ST、Skill 中心、办公工具，页面均有内容；长输入可滚动，发送按钮可见。B 回路修改老化状态、启动一项仿真并查看曲线/历史；不要与高负载 ST 同时压测模型。
7. ST 载入示例，执行注释/检查/测试/修复，等待明确终态并下载原文、注释、修复和报告。失败时查看阶段、模型与验证反馈，不能把“待工程验证”理解成真实 PLC 已通过；真实设备下发不属于本次部署。

每步出错立即处理：

| 现象 | 现场处理 |
| --- | --- |
| 页面打不开/超时 | 服务器用 `curl --noproxy '*' -I http://127.0.0.1:3000/`，再核对 APP_HOST、监听地址、线路和既有防火墙。服务器可访问但客户端不可访问时，核对所内网络规则；只需放行允许客户端到 3000，不开放 14097/4625。 |
| 网络未授权 401 | 客户端真实 IP 须落在 `10.70.0.0/17`（10.70.0.0–10.70.127.255）。用 Windows `ipconfig` 核实；代理会改变真实 TCP 来源。按管理员批准的实际网段修配置，不用 `0.0.0.0/0`，不伪造 X-Forwarded-For。 |
| 登录/写操作 403 CSRF_REJECTED | 地址栏协议/IP/端口须与 DEMO_ORIGIN 一致；Ctrl+F5 或关闭旧标签重新登录获取新 cookie/token。不要禁用 Origin/CSRF 检查。用下面服务器探针区分配置与客户端问题。 |
| 只有某人的资源/文件 403 | 可能是正常资源权限限制；确认创建者、公开状态和账号，不要按网络 403 改白名单。旧历史归初始管理员。 |
| 白屏/按钮无反应 | 先确认新镜像 ID 和 verify，再 Ctrl+F5 / 清除本站缓存、重新登录；确认极速模式。F12 Console 记录第一条脚本错误、浏览器内核版本；不要把旧 assets 单独覆盖进新目录。 |
| 下载 401/403/失败 | 同一浏览器重新登录；确认文件属于此账号/可访问的任务，不把下载链接粘到未登录浏览器。检查客户端下载策略与磁盘；文件已生成不代表浏览器允许下载。 |
| 文件上传 413 | 默认单文件 20 MiB、一次最多 5 个；拆分材料或按审批调整 OFFICE_MAX_FILE_BYTES/OFFICE_MAX_ATTACHMENTS 并 up。扫描 PDF 不会自动 OCR。 |
| 普通聊天成功、任务工具失败 | 运行 probe-model / probe-agent / smoke-office；查工具是否启用、资源是否发布、上下文预算，聊天成功不等于工具链成功。 |
| 生成 Word 失败/没有正文 | 给出明确正文，检查工具执行记录；本版拒绝空正文和未知参数，不能把报错当成功。 |
| B 回路/ST 超时或上下文过长 | 查看日志及任务阶段；确认真实模型窗口和并发负载，缩短源码/附件范围后新建任务。不要盲目增加配置使其超过模型真实上限；不自动重发状态不明请求。 |

服务器认证/来源探针（不登录、不打印密钥）：

```bash
curl --noproxy '*' -sS -D - http://127.0.0.1:3000/api/v1/auth/me
```

预期 HTTP 200，JSON `initialized: true`；`user: null` 对未带 cookie 的 curl 正常。浏览器登录后才拥有会话。非法 Origin、伪造客户端头或内网直连 `/internal/` 返回拒绝是正确保护，不能以“全部请求都 200”为目标。

## 9. 模型业务验收与完成判据

```bash
cd "$TARGET"
sudo bash deploy.sh smoke-office
sudo bash deploy.sh smoke
sudo bash deploy.sh check --wait 300
```

前两条会询问验收账号并真实调用现场模型、写入验收任务。`smoke-office` 做两轮 Excel→汇总→Word 并下载复核，报告在 `data/var/backend/office-acceptance.json`；`smoke` 运行 Monitor/B 回路仿真，报告在 `data/var/backend/real-integration.json`。失败时先看对应报告和日志，保留任务，不用删除数据库重新开始。

完成标准：安装 verify 通过；运行镜像为本次 ID；check ready=true；原内网 Chrome 86 可登录、聊天、切页、上传/下载；两项 smoke 通过；ST 示例达明确、可解释的终态；管理员能查旧历史、成员权限正确。新登录界面、账号隔离和旧共享会话只读查阅属于预期变化。

本机测试结果与边界见 `payload/verification/release-20261021/RESULTS.md`（安装后为 `verification/release-20261021/RESULTS.md`）。真实 Windows 360 企业策略、现场 GPU/vLLM 和光盘读回仍须执行本手册，制作机无法代替这些验收。

## 10. 任何阶段需要回退时

回退需要 `BACKUP-COMPLETE`。将 BACKUP 设置为第 5 节输出的**实际目录**，不要选择其他日期的备份。回退会先停止新服务、将升级后的代码和数据移到备份中的 `failed-state-*`，再恢复升级前整套代码、配置、数据和镜像。升级后新增的数据留在 failed-state 内，旧版不会自动合并它们。

```bash
TARGET=/opt/opencode-agent-demo-deploy
STAGE="$HOME/intranet-update-20261021"
BACKUP="$TARGET/backups/intranet-20261021-AbCd1234"
sudo bash "$STAGE/scripts/intranet-update.sh" rollback "$TARGET" "$BACKUP"
cd "$TARGET"
sudo bash deploy.sh up
sudo bash deploy.sh check --wait 300
```

回退后浏览器 Ctrl+F5；如果原版没有账号系统，原来的共享入口重新出现是正常现象。回退不能只换镜像继续用 0007 数据库。`BACKUP-SHA256SUMS` 校验不通过时停止，先排查磁盘/恢复可靠副本；不要改校验和。回退命令中途失败时保持服务停止，保留所有 failed-state 目录和日志，由管理员根据 `code.tar.gz`、`state.tar.gz`、`old-image.tar.gz` 恢复；不要反复盲跑并删除已有目录。

## 11. 验收后保留和清理

现场保留原部署目录、现场配置与 data、这次完整回退目录、原始光盘/收件目录直到业务确认稳定。不要执行 docker system prune、删除旧镜像、清空 data 或盲删 backups。之后按所内备份周期保留升级前快照；日常备份仍用 `sudo bash deploy.sh backup`。

制作机这次可以清理已被替代的四个中间交付目录，具体清理记录在 `verification/release-20261021/cleanup.json`。历史代码快照/测试证据和模型文件保留；本次有效刻盘入口仅 `delivery/intranet-20261021/disc-01/`。
