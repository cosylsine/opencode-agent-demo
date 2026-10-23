> 历史版本说明：本次累计更新及现场操作请使用 [20261021 手册](INTRANET-UPDATE-20261021.md)。旧刻盘目录已被替代，不要再次安装。

> 办公第一版升级：请以 [office-v1.md](office-v1.md) 为准。本文保留历史排错和交付记录，不要用旧代码片段覆盖新版本的登录与权限实现。

# B 回路调优首轮停止修复

反馈：`FB-20261016-5e21d773291d`。修复日期：2026-10-18。
本次修改的是当前离线部署包，没有连接或修改 `10.70.100.220`。

## 1. 这次究竟要把什么刻盘带到所里

**你的情况是“已有内网部署，带盘更新”。本次只需一张数据 DVD。**

在当前这台准备刻盘的电脑上，要刻录的目录是：

```text
/home/xjx/opencode-agent-demo-deploy/delivery/b-loop-update-20261018/disc-01/
```

**将 `disc-01` 里面的全部内容放到光盘根目录**。盘上应有下面四项，`patch-files` 必须作为一个完整目录一起刻入：

```text
光盘根目录/
├── START-HERE.txt          # 在所里先读这个文件；它包含本手册
├── DISC-SHA256SUMS         # 校验本张光盘全部交付文件
├── UPDATE-FILES.txt        # 会复制到现有部署目录的文件清单
└── patch-files/            # 实际更新文件，按原项目的相对路径摆放
    ├── .dockerignore      # 隐藏文件，也要刻入
    ├── Dockerfile
    ├── deploy.sh
    ├── README.md
    ├── fix-tuning.md
    ├── fix403.md
    ├── SHA256SUMS
    ├── images/
    │   ├── offline-images.tar.gz  # 已修复的应用镜像和原依赖镜像
    │   ├── IMAGE-MANIFEST.json
    │   └── SHA256SUMS
    ├── build/              # 更新后的启动程序
    ├── runtime/ripgrep/    # 离线 rg 二进制、原始归档和许可证
    ├── source/             # 本次及此前内网补丁涉及的源码文件
    ├── config/             # 只含配置示例，不含现场实际配置
    ├── scripts/            # 更新后的制盘脚本
    ├── verification/       # 更新校验清单和验收记录
    └── vllm/README.md      # 只有说明文档，不含备用 vLLM 大镜像
```

这个目录约 **330 MiB，低于 400 MB**，一张普通 4.7 GB 数据 DVD 足够。
这里的树用于说明主要文件；完整清单以盘上的 `UPDATE-FILES.txt` 为准。

将“更新后的部署包”和几个相关目录明确区分：

| 名称 | 实际位置 / 内容 | 本次怎么用 |
| --- | --- | --- |
| 完整部署目录 | 当前电脑的 `/home/xjx/opencode-agent-demo-deploy/`，包含源码、应用镜像、Docker 和约 11 GB 的备用 vLLM 文件等 | 是制作更新盘的来源；本次无需整目录重刻 |
| **本次单盘更新目录** | 上面的 `delivery/b-loop-update-20261018/disc-01/` | **本次就刻这个目录里面的全部内容** |
| 内网现有部署目录 | 所里服务器上正在运行本项目的那个目录，绝对路径由第 4.1 节查出 | 把盘中的更新文件覆盖到这里；继续沿用它的配置和数据 |

单盘更新目录只包含更新文件，**不能代替完整部署包给空白服务器首次安装**。
内网现有的 Docker、vLLM、模型权重和原部署目录都继续使用。
本盘不带 `data/`、`backups/`、`config/demo.env`、`config/vllm.env`，也不带当前电脑的运行凭据。

## 2. 出发前：准备和刻录

### 2.1 使用已准备好的目录

本次已生成的刻盘目录就是第 1 节给出的绝对路径。
在文件管理器进入该目录，应该能看到 `START-HERE.txt`、`DISC-SHA256SUMS`、`UPDATE-FILES.txt` 和 `patch-files/`。

**如果在这台电脑的 Windows 里刻盘：**按 `Win+E` 打开资源管理器，将下面这条实际 WSL 路径粘贴到地址栏，然后按回车：

```text
\\wsl.localhost\Ubuntu-20.04\home\xjx\opencode-agent-demo-deploy\delivery\b-loop-update-20261018\disc-01
```

如果刻录软件不能直接读取这个 WSL 路径，就先在 Windows 桌面新建一个
`b-loop-update-20261018` 文件夹，把看到的四项内容全部复制进去，再把该文件夹内的全部内容添加到数据 DVD 项目。
复制后仍应是三个说明/清单文件加一个完整的 `patch-files` 目录，不要只复制几个看起来重要的文件。

如果以后又改了源码或本文档，需要重新生成制盘目录。在**当前这台准备刻盘的 Linux 电脑**执行：

```bash
cd /home/xjx/opencode-agent-demo-deploy

# 如已有上一份制盘目录，先改名保留，避免把旧、新文件混到一起。
if [ -d delivery/b-loop-update-20261018 ]; then
  mv delivery/b-loop-update-20261018 \
    "delivery/b-loop-update-20261018-old-$(date +%Y%m%d-%H%M%S)"
fi

python3 scripts/make-tuning-disc.py
```

脚本先运行当前项目的完整校验，再复制更新文件、生成光盘校验清单并复核。
如果改过文件但还没有更新项目校验清单，脚本会停止，不能沿用旧校验制作新盘。
如果只改了本文档，不需要重建应用镜像；如果改了应用源码，应先完成重建和验证再制盘。

### 2.2 在刻录软件里添加什么

1. 选择“数据 DVD”项目。它是普通文件光盘，不是启动盘；无需制作启动镜像。
2. 将 `disc-01` 内的**四项内容**全部添加到刻录项目根目录，保持 `patch-files` 的目录结构。
3. `patch-files` 内有 `.dockerignore`、`source/.opencode/` 等隐藏文件，刻录软件不能过滤它们。
4. 不要解压 `images/offline-images.tar.gz`，原样刻入。
5. 完成刻录并封盘，启用刻录软件的写后校验。盘面可标注“B 回路修复 2026-10-18 / 更新盘 / 1 张”。

写后校验完成后，打开光盘，确认根目录就是上面四项。
本次不用运行 `scripts/make-dvd.sh`；那个脚本用于把包含备用 vLLM 的完整安装包分成多张盘。

## 3. 到所里：先复制到服务器硬盘并校验

**从这一节起，所有命令都在所里的 Linux 服务器终端执行。**
Windows 客户端后面只负责打开网页。以下各节请在同一个 Linux 终端执行，后续命令会复用前面设置的变量。
每个代码块成功后再继续；出现错误就停在该步，先处理错误。

插入光盘后，查看挂载位置：

```bash
lsblk -o NAME,TYPE,SIZE,MOUNTPOINT
```

找到光驱对应行的 `MOUNTPOINT`。下例假定为 `/media/cdrom`；如果实际是
`/media/用户名/光盘名`，只需把下面第一行改成那个实际路径：

```bash
TUNING_DISC=/media/cdrom
ls -l "$TUNING_DISC/START-HERE.txt" "$TUNING_DISC/DISC-SHA256SUMS"
```

如果光驱没有挂载位置，且服务器按 `/dev/sr0` 识别该光驱，可以这样只读挂载：

```bash
sudo mkdir -p /media/cdrom
sudo mount -o ro /dev/sr0 /media/cdrom
TUNING_DISC=/media/cdrom
```

将整张更新盘复制到本地硬盘暂存目录，包含隐藏文件：

```bash
TUNING_STAGE="$HOME/offline-updates/b-loop-update-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$TUNING_STAGE"
cp -a "$TUNING_DISC/." "$TUNING_STAGE/"
chmod -R u+rwX "$TUNING_STAGE"
cd "$TUNING_STAGE"
sha256sum -c DISC-SHA256SUMS --quiet
```

最后一条**无输出且返回成功是正常结果**。如果报告 `FAILED`、文件缺失或读取错误，重新检查光盘和复制过程，先不要覆盖服务器文件。
暂存目录只是存放更新文件，不能在其中运行 `deploy.sh up`；服务仍从原部署目录启动。

## 4. 查出原部署目录并做备份

### 4.1 不猜部署路径，从正在运行的容器读取

先找到 Docker 命令，再读取现有容器记录的部署目录：

```bash
TUNING_DOCKER_BIN=$(command -v docker || true)
if [ -z "$TUNING_DOCKER_BIN" ]; then
  TUNING_DOCKER_BIN=/opt/opencode-demo-docker/28.5.2/docker
fi

TUNING_DEPLOY_DIR=$(sudo "$TUNING_DOCKER_BIN" inspect \
  --format '{{index .Config.Labels "io.opencode-demo.bundle"}}' opencode-agent-demo)

printf '现有部署目录：%s\n' "$TUNING_DEPLOY_DIR"
cd "$TUNING_DEPLOY_DIR"
pwd
ls -ld deploy.sh config/demo.env data/var data/catalog
```

例如第一条输出可能是 `/home/某用户/opencode-agent-demo-deploy`，也可能是 `/data/opencode-agent-demo-deploy`；以现场输出为准。
最后一条应同时找到部署脚本、实际配置和两个数据目录。
若容器不存在、输出为空、`<no value>` 或找不到文件，不要新建一个空的 `data/` 继续；先找到现场原来使用的部署目录。

部署脚本会检查容器归属目录，所以不能把更新盘当作新的部署目录另起一套服务。

### 4.2 备份现场数据、将被覆盖的文件和正在运行的镜像

确认此时位于上一步查出的原部署目录。先运行原有备份命令：

```bash
sudo bash deploy.sh backup
```

它会短暂停止应用、备份 `config/demo.env`、`data/var` 和 `data/catalog`，再恢复原运行状态。
成功时会打印 `backups/backup-日期-时间.tar.gz`，**记下这个实际文件名**，回退技能目录时会使用。

再单独保留即将覆盖的项目文件。以下备份目录位于服务器当前用户的硬盘目录，不在光盘内：

```bash
TUNING_BACKUP_DIR="$HOME/offline-updates/tuning-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 "$TUNING_BACKUP_DIR"

while IFS= read -r relative; do
  if sudo test -e "$TUNING_DEPLOY_DIR/$relative"; then
    printf '%s\n' "$relative"
  fi
done < "$TUNING_STAGE/UPDATE-FILES.txt" > "$TUNING_BACKUP_DIR/overwritten-files.txt"

sudo tar -czf "$TUNING_BACKUP_DIR/files-before.tar.gz" \
  -C "$TUNING_DEPLOY_DIR" -T "$TUNING_BACKUP_DIR/overwritten-files.txt"

TUNING_OLD_IMAGE=$(sudo "$TUNING_DOCKER_BIN" inspect --format '{{.Image}}' opencode-agent-demo)
TUNING_OLD_TAG="opencode-agent-demo:before-tuning-update-$(date +%Y%m%d-%H%M%S)"
sudo "$TUNING_DOCKER_BIN" tag "$TUNING_OLD_IMAGE" "$TUNING_OLD_TAG"
printf '%s\n' "$TUNING_OLD_TAG" > "$TUNING_BACKUP_DIR/image-tag.txt"
printf '本次项目文件备份：%s\n' "$TUNING_BACKUP_DIR"
```

这里保留的是**容器实际运行的旧镜像**，不假定磁盘上的旧镜像归档一定与运行版本一致。
记下 `TUNING_BACKUP_DIR` 的输出；更新完成前保留旧镜像标签和两类备份。

## 5. 覆盖更新文件、导入新镜像并启动

### 5.1 只复制盘中的 patch-files 内容

```bash
sudo cp -a "$TUNING_STAGE/patch-files/." "$TUNING_DEPLOY_DIR/"
cd "$TUNING_DEPLOY_DIR"
sudo chmod 755 runtime/ripgrep/rg
bash deploy.sh verify
```

`patch-files` 内没有现场实际配置和数据，所以这条复制命令更新代码、镜像、示例配置和文档，
保留服务器原有 `config/demo.env`、`config/vllm.env`、`data/` 和 `backups/`。
未修改的原项目文件继续沿用，故不能先删除原部署目录再复制更新盘。
`chmod` 这一步恢复随光盘传输时可能丢失的 rg 执行权限，便于以后在内网重建镜像。

校验通过时末尾显示“原交付文件及内网同步文件 SHA-256 校验通过”。
该校验也检查原部署中未修改的交付文件；若原目录缺文件或有其他手工改动，按报错核对，不能通过改写校验清单来跳过。

### 5.2 导入修复镜像

```bash
sudo bash deploy.sh load
sudo "$TUNING_DOCKER_BIN" image inspect \
  --format '{{.Id}}' opencode-agent-demo:20261013-amd64
```

应看到两个镜像已导入，接着检查到的应用 ID 必须是：

```text
sha256:3ea044f5b48e9c7e4c3a0c641c3ee0391541ba56b103801a9c5f55adcfb14a97
```

镜像标签仍叫 `opencode-agent-demo:20261013-amd64`，**不能只看这个名称判断是否更新**。
`load` 导入的归档已含 B 回路、内网访问和旧浏览器补丁，这条主流程无需再运行 `rebuild`。

### 5.3 用现有配置和现有数据重建容器

```bash
sudo bash deploy.sh up
sudo bash deploy.sh check --wait 300
sudo "$TUNING_DOCKER_BIN" inspect --format '{{.Image}}' opencode-agent-demo
```

最后一条输出也应是上述新应用 ID，证明新容器确实使用了修复镜像。
`check` 最后应有 `"ready": true` 和“验收通过”。刚启动时需要重新积累约 120 秒采样，超时可以再执行一次 check。
宿主机现有 vLLM 继续使用；这里不会重新安装模型服务，也不会修改模型 ID、密钥、GPU 参数或客户端网段。

启动程序会把三个内置调优文件更新到现场 `data/catalog/`，差异旧版备份到
`data/catalog/.builtin-backups/tuning-v3/`，保留自定义技能。不要手动删除整个 `data/catalog` 来强制刷新。

## 6. 验收：确认第二组候选也能正常运行

### 6.1 在服务器执行专项验收

```bash
cd "$TUNING_DEPLOY_DIR"
sudo bash deploy.sh smoke --min-b-candidates 2
```

该命令会实际调用现场配置的模型，可能需要几分钟，并会新增演示运行记录。
它要求两个 Monitor 场景通过，以及 B 回路至少连续评测两组 Control 候选，
每组均有四工况结果和成功的 MCP 读取审计。首轮后直接 ABSTAIN 不算此次专项回归通过。

成功时输出类似：

```text
Real integration evidence saved to /app/var/backend/real-integration.json
```

容器内这个路径对应服务器原部署目录中的 `data/var/backend/real-integration.json`。可查看报告：

```bash
sudo cat data/var/backend/real-integration.json
```

报告中的 B 回路记录应有 `source_kind: SIMULATION`、`min_b_candidates: 2`，
`trials` 至少为 3，分别是基线加两组候选。
模型在正常业务中仍可合理选择 ABSTAIN；两组候选是这次故障的专项验收门槛，不是强迫每次业务都提出两组的规则。

### 6.2 再用 Windows 浏览器操作一次

1. 在所里 Windows 电脑打开现有地址 `http://10.70.100.220:3000`，刷新页面。
2. 接待对话发送一句话，确认基本对话正常。
3. 进入“B 回路仿真调优”，点击“开始仿真调优”。
4. 查看新运行中是否有基线和后续候选、四工况曲线，以及本轮解释。

旧失败记录会保留，不自动重放。若仍在首轮停止，记录新运行 ID 和停止原因，再在服务器查看：

```bash
sudo bash deploy.sh logs
sudo bash deploy.sh probe-agent
```

如果 `ready` 不通过，先运行 `sudo bash deploy.sh probe-model` 检查现场模型。
若工具正常但模型选择 ABSTAIN，应结合其理由判断；本机 TEST 模型验收不能替代现场 Qwen 的实际判断。

## 7. 如需回退本次更新

仅在需要回退时执行。先把 `TUNING_DEPLOY_DIR`、`TUNING_BACKUP_DIR` 设置为第 4 节记下的实际路径，
`TUNING_DOCKER_BIN` 沿用第 4.1 节查出的命令。随后停止应用并恢复被覆盖的项目文件和旧镜像标签：

```bash
cd "$TUNING_DEPLOY_DIR"
sudo bash deploy.sh down
sudo tar -xzf "$TUNING_BACKUP_DIR/files-before.tar.gz" -C "$TUNING_DEPLOY_DIR"
TUNING_OLD_TAG=$(cat "$TUNING_BACKUP_DIR/image-tag.txt")
sudo "$TUNING_DOCKER_BIN" tag "$TUNING_OLD_TAG" opencode-agent-demo:20261013-amd64
```

还需要恢复更新前的持久化技能，避免旧镜像继续加载新技能。下面第一行的文件名必须替换成
第 4.2 节 `deploy.sh backup` 打印的实际备份文件，不要照抄中文占位值：

```bash
TUNING_DATA_BACKUP="$TUNING_DEPLOY_DIR/backups/backup-实际日期-实际时间.tar.gz"
mkdir -p "$TUNING_BACKUP_DIR/catalog-restore"
sudo tar -xzf "$TUNING_DATA_BACKUP" -C "$TUNING_BACKUP_DIR/catalog-restore" data/catalog
sudo cp -a "$TUNING_BACKUP_DIR/catalog-restore/data/catalog/." "$TUNING_DEPLOY_DIR/data/catalog/"
sudo bash deploy.sh up
sudo bash deploy.sh check --wait 300
```

这里只恢复技能文件，保留现有数据库和更新后新增的运行记录。完整业务数据恢复另见 README 的备份与恢复说明。
回退时不要再执行新盘的 `load`，否则会重新把应用标签指向新镜像。

## 8. 原因与修改（技术说明）

1. **镜像缺少 ripgrep。** OpenCode 1.18.31 的技能加载内部使用 ripgrep 枚举文件。
   原依赖镜像只有 OpenCode，没有 `rg`，离线环境也无法完成下载回退。
   隔离容器实际返回 `ripgrep execution failed`，不是 Agent 缺少 shell 权限。
   现随包提供官方静态 ripgrep 15.1.0，复制到镜像 `/opt/bin/rg`，离线构建时验证可执行和技能枚举。
   版本、来源、原始校验和许可证见 [runtime/ripgrep](runtime/ripgrep/README.md)。
2. **旧项目配置覆盖 MCP 地址。** 原 `opencode.json` 固定回连 `18000`，会覆盖启动器生成的 `3000`。
   请求抵达其他后端时凭据不匹配，工具返回 `TOOL_READ_FAILED`。
   该问题在此前镜像重建时已经修正，本次保留并复测：项目配置与启动器共同读取 `INTERNAL_API_BASE_URL`。
   普通聊天正常和 MCP 已连接均不能证明业务工具可读。
3. **技能与上下文不一致。** 原 `control-safety` 明确限定 “Only EXAMPLE”，与 B 回路 SIMULATION 冲突。
   原 B 上下文没有 `actual_pid`、拒绝记录和剩余时间，历史还被截为最后 12 项。
   现区分 EXAMPLE 与 SIMULATION；B 的 `actual_pid` 明确来自仿真初始参数，不能当成现场实测参数。
   提供全部试验、拒绝记录和预算，指导模型按结果继续判断候选；无可信候选时仍可 ABSTAIN。
4. **持久化技能不会随镜像自动更新。** 启动程序原来只初始化空目录。
   现每次启动同步本次修复涉及的 Control Agent、pid-tuning、control-safety 三个内置文件。
   如旧内容有差异，先按内容摘要备份到 `data/catalog/.builtin-backups/tuning-v3/`；自定义技能保留。
5. **Control 凭据期限覆盖恢复窗口。** 从 300 秒调整为 360 秒，覆盖现有 300 秒 HTTP 调用及 30 秒结果查询。
   调用结束即撤销；取消、迭代/请求不匹配、运行超时均禁止读取。没有扩展 Agent 的工具权限。

## 9. 本机证据与边界

证据目录：[verification/tuning-fix-20261018](verification/tuning-fix-20261018/)。
原 MCP 端口覆盖复现见 `original-tools.json`、`original-errors.json` 和 `original-mcp-environment.json`
（为单独验证端口问题，临时补入了 rg；实际工具返回 HTTP 401）。
已修正端口但尚未修复技能的镜像复现见 `before.json`。
构建、测试、升级和完整业务验收结果记录在该目录。

| 验证 | 结果 |
| --- | --- |
| Python 回归 | 143 项通过，见 `pytest.txt`；Ruff 通过，见 `ruff.txt`。 |
| 离线构建 | `deploy.sh rebuild` 使用 `--network none` 成功；包含 TypeScript/Vite 构建及 rg 运行检查。 |
| 镜像内容 | 应用 ID `sha256:3ea044f5b48e9c7e4c3a0c641c3ee0391541ba56b103801a9c5f55adcfb14a97`；10 项源码/二进制摘要与交付文件一致，见 `image-check.json`。 |
| 已有目录升级 | 三个旧内置文件已备份并更新，自定义技能保留，见 `catalog-upgrade.json`。 |
| 完整业务 | 两个 Monitor 场景通过；B 基线加两组候选共 3 次四工况 trial，两组候选均安全且改善得分，见 `integration.json`、`after.json`。 |
| 实际工具 | 10 次技能加载、9 次 MCP 读取全部成功，其中 3 次为 Control 上下文；逐轮历史分别为 `[0]`、`[0,1]`、`[0,1,2]`。 |

验收使用真实容器、OpenCode、技能加载、MCP、后端和四工况仿真程序，模型由明确标记的 TEST 协议服务替代。
该服务给出固定候选，证明工具和多轮链路可用，不验证现场 Qwen 的推理质量或选择候选的能力。
原包的协议服务即使收到工具错误仍可继续给出固定候选，所以原包证据只用于确认工具故障，不能用其终态模拟真实模型行为。
现场最终结果仍应以上述双候选 smoke 和浏览器操作为准。

**当前准备刻盘电脑上**的 `backups/tuning-fix-20261018/` 保存开发修复前的相关文件和镜像归档，
该机 Docker 也保留 `opencode-agent-demo:before-tuning-fix-20261018` 标签。
这些开发机备份不随更新盘交付；现场回退使用第 4 节在现场新建的备份。本次没有修改真实设备参数。
