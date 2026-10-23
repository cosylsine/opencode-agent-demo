# 部门智能体平台离线部署包

**当前交付：20261021-intranet-amd64。** 已部署并修好 403 / Chrome 86 的内网服务器，只使用 `delivery/intranet-20261021/disc-01/` 这一张累计更新 DVD。无需安装中间版本。

**现场升级以 [2026-10-21 逐步手册](INTRANET-UPDATE-20261021.md) 为准。** 包含刻盘、复制、预检查、停服备份、账号初始化、启动、403/旧浏览器排障、业务验收和整套回退。最新测试及限制见 [本次验收报告](verification/release-20261021/RESULTS.md)。

本次已重建应用和办公依赖归档。`deploy.sh verify` 优先校验 `RELEASE-SHA256SUMS`；旧清单保留为历史证据。后面的首次安装通用参考及旧文档不替代本次升级手册。

**从这个文件夹即可完成安装，无需联网下载。适用：Linux x86_64（Intel/AMD），root 或 sudo 权限。**

当前内网部署地址：**http://10.70.100.220:3000**；OpenCode 使用回环端口 **14097**，宿主机 vLLM 使用回环端口 **4625**。不需要 Tailscale、Compose 或宿主机 Python/Node。
包内含 Docker 28.5.2、应用镜像、完整依赖镜像、源码、构建文件和检测脚本。Python 3.13.2、Node 22.23.3、OpenCode 1.18.31 已装进镜像。

**累计更新盘：**仅刻 `delivery/intranet-20261021/disc-01/` 内全部内容，按 [现场手册](INTRANET-UPDATE-20261021.md) 执行。归档包含标签 `opencode-agent-demo:20261021-intranet-amd64` 及办公依赖镜像，导入后无需现场构建。保留原机模型配置、账号和数据，新增账号初始化按手册进行。

以下是完整部署文件夹的通用安装参考；更新盘不含备用 vLLM 镜像/模型，不能用于空白服务器完整安装。

**模型接入有两种情况：**现场已有 vLLM 时直接按下面步骤连接；现场没有时，本包也附带备用 vLLM 镜像和 GPU 接入组件，按 [备用 vLLM 手册](vllm/README.md) 启动。使用内网现有 Qwen3.8-27B 权重，宿主机 NVIDIA 驱动须正常；本包不带权重和驱动。模型未启动时网页仍可打开，但 Agent 暂不可用。镜像准备和离线测试情况见 [验收记录](verification/RESULTS.md)。

## 到内网后，按顺序操作

### 1. 从光盘复制到本地硬盘

**从旧 DVD 部署时：先按每张光盘里的 `RESTORE.txt` 把分卷复制到同一个硬盘目录，然后执行 `bash restore.sh`，还原完整部署文件夹。** 旧光盘不含本次补丁，应使用当前更新后的部署包，或按 `fix403.md` 修改并在导入旧镜像后执行 `rebuild`；仅还原旧光盘仍是原版。还原后进入部署目录运行 verify，再继续第 2 步。

若光盘上是完整目录，可直接复制。不能从只读光盘运行；下例假定光盘挂在 `/media/cdrom`：

```bash
cp -r /media/cdrom/opencode-agent-demo-deploy "$HOME/"
chmod -R u+rwX "$HOME/opencode-agent-demo-deploy"
cd "$HOME/opencode-agent-demo-deploy"
bash deploy.sh verify
```

看到 SHA-256 校验通过再继续。必须复制**整个文件夹**，包括隐藏文件 `.dockerignore`。后面每条命令都在该目录执行。只跑应用至少留 10 GB；使用备用 vLLM 时，建议整个硬盘预留 **100 GB 以上**（不含已有模型权重，分卷还原和镜像解压需要额外空间）。使用本地 ext4/XFS 等支持文件锁的文件系统，不要使用光盘、NFS、CIFS 或 Windows 共享目录存 SQLite。

### 2. 离线安装 Docker，再导入镜像

先用 `df -h . /var/lib` 检查空间：部署文件夹和 Docker 镜像目录可能在不同分区。若要启用备用 vLLM，Docker 所在分区建议至少有 60 GB 空闲空间。

```bash
sudo bash scripts/install-docker.sh
sudo bash deploy.sh load
```

当前归档已经包含内网和旧浏览器补丁，导入后即可继续配置。只有使用旧 DVD/旧归档，或再次修改源码时才需 `sudo bash deploy.sh rebuild`；该命令使用已导入的依赖镜像完全离线构建。

若 `/var/lib` 所在分区太小，可在**第一次安装 Docker 前**把上面的安装命令替换为下列命令，将镜像放到大容量的本地磁盘（修改 `/data` 为实际挂载点）：

```bash
sudo env DEMO_DOCKER_DATA_ROOT=/data/opencode-demo-docker bash scripts/install-docker.sh
```

安装脚本使用 systemd，自动设定 Docker 开机启动。已有可用 Docker 时直接复用，不覆盖其配置。新装 Docker 专用于本项目的 host 网络，关闭网桥/NAT，因此不需要额外安装 iptables。不要运行 `docker run hello-world`，那会尝试下载镜像。
校验、导入大镜像期间可能数分钟没有新输出，请等待命令结束；使用备用 vLLM 时尤其如此。所有 `sudo` 命令也可在 root 终端去掉 `sudo` 执行。

### 3. 修改一个配置文件

```bash
bash deploy.sh init
nano config/demo.env
```

没有 nano 就用 `vi config/demo.env`，或使用系统的文本编辑器。主要修改：

```dotenv
VLLM_BASE_URL=http://127.0.0.1:4625/v1
VLLM_MODEL=Qwen3.8-27B
VLLM_API_KEY=EMPTY
APP_PORT=3000
APP_HOST=0.0.0.0
DEMO_ORIGIN=http://10.70.100.220:3000
DEMO_ALLOWED_CIDRS=10.70.0.0/17
OPENCODE_PORT=14097
```

- 当前宿主机 vLLM 端口为 `4625`；**保留 `/v1`**。Linux host 网络让容器的 `127.0.0.1` 直接指向宿主机，无需 `host.docker.internal`。[Docker 官方说明](https://docs.docker.com/engine/network/drivers/host/)
- `VLLM_MODEL` 必须等于服务返回的模型 ID，区分大小写。例如实际 ID 可能是 `Qwen/Qwen3.8-27B`，甚至是模型目录的绝对路径。下一步会列出准确值。
- 没设置 vLLM `--api-key` 时保留 `EMPTY`；设置了就填真实密钥。
- `VLLM_CONTEXT=32768` 不要超过 vLLM 的 `--max-model-len`。值使用 `KEY=value` 格式，**不加引号、不写 export、不在值后写注释**。
- `APP_HOST=0.0.0.0` 启用内网直连；`DEMO_ORIGIN` 必须与浏览器中的协议、服务器地址、`APP_PORT` 一致，末尾不加 `/`。更换网页端口时同时修改这两项。OpenCode 仍只在本机回环监听，修改 `OPENCODE_PORT` 后内部地址会自动同步。
- `DEMO_ALLOWED_CIDRS` 控制实际 TCP 客户端 IP；当前为现场确认的 `10.70.0.0/17`；更换现场时按实际网段修改。可用逗号配置多个网段，或以客户端 `IP/32` 只允许一台。白名单控制网络入口；用户还须注册登录，`/internal/` 仍仅允许回环来源。

### 4. 验证宿主机 vLLM

如果还没有可用的 vLLM，先完成 [备用 vLLM 部署](vllm/README.md)，再回到这里。确保 vLLM 已完成模型加载，然后执行：

```bash
sudo bash deploy.sh models
sudo bash deploy.sh probe-model
```

第一条列出模型 ID。如果不匹配，回到第 3 步修改。第二条验证文本、JSON Schema、自动工具调用和指定工具调用，最后应显示 **`"ready_for_agents": true`**。

如果现场需要启动 vLLM，下列是**已有 vLLM 环境和本地权重**的参数参考；模型路径、显卡和并行数按现场资源调整，不要在断网机器执行 pip install 或使用远程模型名下载：

```bash
vllm serve /实际模型目录/Qwen3.8-27B \
  --host 127.0.0.1 --port 4625 \
  --served-model-name Qwen3.8-27B \
  --max-model-len 32768 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 \
  --default-chat-template-kwargs '{"enable_thinking": false}'
```

工具解析器参考 [vLLM 的 Qwen3.8 官方配方](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)，默认关闭思考的参数见 [vLLM CLI](https://docs.vllm.ai/en/latest/cli/serve/)。现场版本必须支持该模型和这些参数。普通聊天成功不代表 Agent 工具调用成功，以上探针必须实测通过。

### 5. 启动并等待就绪

```bash
sudo bash deploy.sh init-admin  # 仅在尚未初始化管理员时执行，服务须停止
sudo bash deploy.sh up
sudo bash deploy.sh check --wait 300
```

首次启动自动生成服务凭据、数据库和技能目录；示例采样需要连续积累约 **120 秒**，模型就绪探测每 60 秒执行一次。
最终看到 **`"ready": true`** 和“验收通过”即可。超时可重复执行 check；不会清空数据。如果模型还没有准备好，可先启动看页面，完成第 4 步后再验收。

### 6. 从内网电脑打开浏览器

内网 Windows 电脑直接打开 **http://10.70.100.220:3000**（更改服务器或网页端口时使用对应的 `DEMO_ORIGIN`）。先在接待对话中发送一句“你好”，再按需尝试 Monitor 检查和 B 回路仿真调优。360 浏览器使用极速内核；当前已包含 Chrome 86 所需的数组/Markdown 兼容方法和高度回退。

这是仿真演示，没有真实设备参数下发。服务器防火墙只需按现场策略向允许的客户端放行 TCP 3000，无需开放 OpenCode 14097 或 vLLM 4625；具体排查和规则示例见 [fix403.md](fix403.md)。如需通过 SSH 转发访问，可在内网电脑执行：

```bash
# 两个 3000 都必须与 APP_PORT 一致；随后浏览器打开 http://127.0.0.1:3000。
ssh -N -L 3000:127.0.0.1:3000 用户名@10.70.100.220
```

SSH 转发两端保持相同端口，以通过网页 Origin 校验；若内网电脑的该端口被占用，可修改服务器 `APP_PORT`、重新 up，再同时修改转发命令中的两个端口。

需要完整业务验收时运行 `sudo bash deploy.sh smoke`；这会实际调用模型并生成 Monitor/B 回路仿真记录，可能需要几分钟，报告写到 `data/var/backend/real-integration.json`。

## 日常操作

```bash
sudo bash deploy.sh status             # 容器运行状态
sudo bash deploy.sh check              # 模型、MCP、采样、数据库是否 ready
sudo bash deploy.sh logs               # 最近 150 行日志
sudo bash deploy.sh logs -f            # 持续日志，Ctrl-C 仅退出查看
sudo bash deploy.sh stop               # 停止，数据保留
sudo bash deploy.sh up                 # 启动；改配置后也用这一条
sudo bash deploy.sh backup             # 短暂停服备份，然后恢复原运行状态
```

`healthy` 只表示本地进程和 API 正常；完整可用性以 `check` 的 `ready` 为准。
更改 `config/demo.env` 后必须运行 `up` 或 `restart`，让新配置进入容器。无需再次 load。
正常运行的容器随 Docker/系统重启自动启动；手动 stop 过的容器需手动 up。宿主机 vLLM 的开机启动由其原有服务管理。

数据和技能均在 `data/`，重建容器不会丢失；不要删除它。备份在 `backups/`，包含 `config/demo.env`、`data/var`、`data/catalog` 和其中的密钥，按部署数据保存；不备份模型权重和 vLLM 编译缓存。恢复时保留现有应用数据，再在当前目录解压备份：

```bash
sudo bash deploy.sh down
stamp=$(date +%Y%m%d-%H%M%S)
sudo mv data/var "data/var-before-$stamp"
sudo mv data/catalog "data/catalog-before-$stamp"
sudo tar -xzf backups/实际备份文件名.tar.gz
sudo bash deploy.sh up
```

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 校验失败 / unexpected EOF / 光盘文件不全 | 不继续安装；重新完整复制，重新运行 verify。本同步版保留原清单并另有 LAN-SYNC-SHA256SUMS；不要手工改清单掩盖文件损坏。 |
| 还原中断 / No space left on device | 检查部署目录和 Docker 数据目录两个分区。补足空间；还原中断时先把未完成的部署目录重命名，再重新运行 restore.sh。 |
| Permission denied / 只读文件系统 | 先复制到本地硬盘。命令使用 `bash 脚本名`；Docker 操作用 sudo。 |
| Docker 无法连接 | `sudo systemctl status opencode-demo-docker --no-pager` 和 `sudo journalctl -u opencode-demo-docker -n 60 --no-pager`。若是原有 Docker，服务名为 `docker`。 |
| 不支持 overlay2 / cgroup | 需要支持容器的 Linux 内核和本地磁盘；建议内核 5.4 以上。老 XFS 须支持 d_type。若仅 overlay2 不支持，可将本包 systemd unit 的 `--storage-driver=overlay2` 改为 `vfs`，daemon-reload、重启服务并重新 load；vfs 占更多磁盘。不要删除原数据目录。 |
| 无 systemd | 使用下文手动 Docker 启动步骤；不自动提供开机启动。 |
| 页面端口被占用 | 修改 `APP_PORT` 后重新 up；浏览器使用新端口。OpenCode 冲突则修改 `OPENCODE_PORT`。无需停止其他服务。 |
| 容器正常，但用服务器 IP 打不开 / 返回 401 | 确认已 rebuild 并 up；检查 APP_HOST、DEMO_ORIGIN、客户端实际 IP 是否在 DEMO_ALLOWED_CIDRS 内，以及服务器防火墙。详见 fix403.md。 |
| models 拒绝连接或超时 | 确认 vLLM 加载完成、端口准确、宿主机同端口确实可访问；vLLM 若在另一个容器，须已发布该宿主机端口。 |
| HTTP 401/403 | 检查 `VLLM_API_KEY`；网页写操作 403 时确认地址/端口正确，刷新页面取得新 CSRF token。 |
| model not found / 模型不匹配 | 运行 models，逐字复制返回的 ID 到 `VLLM_MODEL`，重新 up。 |
| probe-model 的 tools 或 JSON Schema 不通过 | 检查 vLLM 版本、工具解析器和模型聊天模板；使用第 4 步的参数，不能仅验证普通文本聊天。 |
| tokens/context 超限 / 推理超时 | `VLLM_CONTEXT` 不大于服务实际上下文；模型需有足够输入和输出预算。关闭思考、检查 GPU 显存/并发，必要时降低输出长度。 |
| ready=false / 页面显示 degraded | 首启需要连续 120 秒采样；正在导入大镜像、磁盘很忙时可能因采样中断延长等待，导入结束后再等两分钟。check 查看失败组件；probe-model 排除 vLLM，再用 `sudo bash deploy.sh probe-agent` 检查 OpenCode/MCP/结构化调用，最后看 logs。 |
| 进程不断重启 / 数据库锁冲突 | 查看 logs；同一个 data 目录只启动一份容器、一个后端 worker。不要从网络盘运行。 |
| Docker healthy 但模型断开 | healthy 是容器存活检查；用 check 看业务状态。模型恢复后后台会重新探测。 |
| OpenCode Illegal instruction | 目标 CPU 指令集与此 x86_64 二进制不兼容；需要支持其指令集的机器。刻盘前可在同规格机器验证。 |
| 系统启用了 SELinux | 启动脚本为数据挂载加了 `:Z`。若仍被策略拒绝，由管理员按本机容器策略配置；不要通过关闭 SELinux 掩盖问题。 |
| 停机后 Monitor 暂不可用 | 采样不回填，重新积累 120 秒；旧记录保留，B 回路仿真独立运行。 |

### 没有 systemd 时启动 Docker

仅适用于尚未安装 Docker 的完整 Linux 主机。以 root 运行以下命令；保持最后一个命令所在终端开启，另开终端从第 2 步 `load` 继续：

```bash
sudo mkdir -p /opt/opencode-demo-docker/28.5.2
sudo tar -xzf runtime/docker-28.5.2-linux-x86_64.tgz \
  -C /opt/opencode-demo-docker/28.5.2 --strip-components=1
sudo env PATH=/opt/opencode-demo-docker/28.5.2:/usr/sbin:/usr/bin:/sbin:/bin \
  /opt/opencode-demo-docker/28.5.2/dockerd \
  --host unix:///var/run/docker.sock \
  --data-root /var/lib/opencode-demo-docker \
  --exec-root /run/opencode-demo-docker \
  --pidfile /run/opencode-demo-docker.pid \
  --bridge none --iptables=false --ip-forward=false --ip-masq=false \
  --storage-driver overlay2
```

## 包内文件与离线改代码

| 路径 | 用途 |
| --- | --- |
| `README.md` | 现场以本手册为准，源码中的旧 WSL/tailnet 文档是历史参考 |
| `runtime/` | 官方 Linux x86_64 Docker 静态安装包 |
| `images/offline-images.tar.gz` | 应用镜像 + 含完整依赖的基础镜像，一次 load 全部导入 |
| `vllm/`、`vllm.sh` | 可选的 H100 vLLM 镜像、管理入口，以及原版 Qwen3.8-27B 配置/分词器备份（不含权重） |
| `runtime/nvidia-cdi/` | NVIDIA GPU 容器接入工具（无需现场 apt/yum） |
| `dvd/disc-*/`（可选） | 分卷运输/刻录副本，运行和重建均不依赖；原分卷在导入并验收后可删除，需要时可重新制作 |
| `config/demo.env.example` | 配置模板；init 生成供你修改的 demo.env |
| `source/` | 项目源码、Agent、Skill、测试和前端源码 |
| `Dockerfile`、`build/` | 完全离线的应用构建配方和容器进程管理程序 |
| `verification/` | 制作环境的测试证据与离线验收工具 |
| `verification/LAN-SYNC-SHA256SUMS` | 2026-10-18 同步文件校验清单；原交付清单 SHA256SUMS 保持不变 |
| `data/`、`backups/` | 现场运行后产生，不包含当前开发机的历史数据 |

修改 `source/` 或 `build/` 后，可运行 `sudo bash deploy.sh rebuild` 再 `sudo bash deploy.sh up`。重建以 `--network none` 执行，使用包内已导入的依赖镜像，前端也能离线重新编译；不需要 pip/npm 下载。新增或升级第三方依赖则需在联网环境重新制作依赖镜像。本包使用 Docker 官方静态二进制，升级也需要重新带入离线包，参见 [Docker 静态安装说明](https://docs.docker.com/engine/install/binaries/)。

完整目录已还原到硬盘、镜像已导入且服务验收通过后，`dvd/` 和单独复制的 `offline-parts/` 分卷可删除；它们只负责运输和还原。`data/`、`config/`、`backups/`、`source/`、`build/`、`images/`、`runtime/` 和 `vllm/` 应保留，以便维护、离线重建和恢复。当前项目中原有的 `dvd/` 是旧版分卷，不能直接作为修复版交付物。

再次刻盘时，运行 `bash deploy.sh verify`，再执行 `bash scripts/make-dvd.sh` 重新生成分卷。每个 `dvd/disc-XX` 单独刻为数据 DVD，建议 UDF，保留文件名，完成封盘并启用刻录软件的写后校验；不要解压分卷再刻录。当前归档镜像已包含补丁，导入即可使用。后续配置、业务数据和备份保存在硬盘副本里。

## 在本机模拟验收

需要实际运行 Docker。`bash verification/run-rehearsal.sh up` 会用当前配置启动真实应用镜像，另行挂载 TEST 模型辅助程序；再运行 `bash verification/run-rehearsal.sh check --wait 360` 和 `bash verification/run-rehearsal.sh smoke`。本机页面为 `http://127.0.0.1:3000`，相关端口须空闲。

这用于检查部署链路，不进行真实模型推理。模拟数据使用独立临时内存盘，不写入正式 `data/`；通过 `bash verification/run-rehearsal.sh stop` 清理模拟容器。自定义 Docker socket 环境需设置对应的 `DOCKER_HOST`。详细步骤、验收范围和限制见 [重建记录](verification/REBUILD.md)。
