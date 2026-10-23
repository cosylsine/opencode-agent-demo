# 备用 vLLM：H100、完全离线、复用内网权重

**现场已有可用 vLLM 时跳过本章。**这里准备的是官方 vLLM 0.31.0 / CUDA 12.9 的 Linux amd64 镜像和 NVIDIA Container Toolkit 1.20.1 的 CDI 工具。权重直接挂载内网现有目录，不进行下载。

目标为 8 卡 H100，默认选其中 `0,1` 两张空闲卡、张量并行 2、上下文 32768。实际显存和其他任务占用以 `nvidia-smi` 为准。本机没有 H100 和现场权重，真实 GPU 推理必须在现场验收，不能将镜像导入成功视作模型成功。
默认启用纯文本服务，关闭思考和 CUDA Graph，减少首启资源开销；适用于本项目的对话与 Agent 工具调用。参数根据 [Qwen3.8 官方配方](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) 配置。

脚本会自动加载 `vllm/compat/` 中的媒体组件兼容补充，解决该版本官方镜像的 `libnvrtc.so.13` 启动错误；不改变用于模型推理的 PyTorch/CUDA。该目录也须随包复制，无需现场 pip 安装，来源见 `COMPAT-MANIFEST.json`。

## 现场步骤

在部署目录执行，先完成主 README 的 Docker 安装和应用镜像 load。

1. 确认宿主机驱动工作正常，并准备 GPU 容器支持：

   ```bash
   nvidia-smi
   sudo bash scripts/install-gpu-support.sh
   sudo bash vllm.sh load
   ```

   安装脚本仅复制本包 NVIDIA 工具并生成专用 CDI 设备描述，不安装/升级驱动，不需要 apt/yum，不覆盖原有 NVIDIA 容器运行配置。本包 Docker 28.5.2 已支持 CDI。参考 [Docker CDI](https://docs.docker.com/reference/cli/docker/container/run/) 和 [NVIDIA CDI](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)。

2. 生成并修改配置：

   ```bash
   bash vllm.sh init
   nano config/vllm.env
   ```

   重点修改 `MODEL_DIR=/内网完整模型目录`；目录内应有 `config.json`、tokenizer 文件和全部 safetensors 权重分片。Hugging Face 缓存的 snapshot 若含符号链接，需复制为自包含目录，例如 `cp -aL 原snapshot目录 /本地/Qwen3.8-27B`，否则挂进容器后链接可能失效。

   若内网只有**官方原版 Qwen3.8-27B 权重**、缺配置或分词器，本包还带有这些文件的备份，可执行：

   ```bash
   sudo bash scripts/prepare-model-metadata.sh /内网完整模型目录
   ```

   该命令只补缺失文件，保留已有文件；权重分片名称须与索引一致。量化版、改结构或自定义分片应使用它们自己匹配的配置与索引。备份来源和固定版本见 `vllm/MODEL-METADATA.json`。

   `GPU_IDS=0,1` 可改为 `2,3` 等；`TENSOR_PARALLEL_SIZE` 必须等于所选 GPU 数。先选空闲卡。`SERVE_PORT` 模板为 4625，与当前内网部署一致；已有服务占用时不要停止它，换端口或者直接复用该服务。

3. 不加载权重，先测试容器内 CUDA：

   ```bash
   sudo bash vllm.sh gpu-check
   ```

   看到 GPU 列表、PyTorch/CUDA 版本，且**每张所选卡**都显示 `GPU computation= 32.0` 才继续。这一步验证容器设备和驱动可用；尚未验证模型、并行通信和显存容量。

4. 启动模型服务并查看日志：

   ```bash
   sudo bash vllm.sh up
   sudo bash vllm.sh logs -f
   ```

   初次载入权重和编译 GPU 内核可能需要 5–20 分钟或更久。日志显示 API 服务已启动后，用 Ctrl-C 退出日志查看（不会停止服务）。模型目录只读挂载；缓存写到 `data/vllm-cache/`。

5. 让应用接入刚启动的服务：

   编辑 `config/demo.env`，使以下三项与 `config/vllm.env` 一致：

   ```dotenv
   VLLM_BASE_URL=http://127.0.0.1:4625/v1
   VLLM_MODEL=Qwen3.8-27B
   VLLM_API_KEY=EMPTY
   ```

   ```bash
   sudo bash deploy.sh probe-model
   sudo bash deploy.sh up
   sudo bash deploy.sh check --wait 300
   ```

   `probe-model` 最后须为 `ready_for_agents: true`，应用 `check` 须为 `ready: true`。随后回主 README 的浏览器访问步骤。

## 驱动、显存和常见失败

- **驱动未知：**先用 `nvidia-smi` 确认版本。镜像包含 CUDA 前向兼容库，配置默认开启 `VLLM_ENABLE_CUDA_COMPATIBILITY=1`，用于 H100 等数据中心 GPU 的受支持旧驱动组合；不是任意驱动都能兼容。以 `gpu-check` 的 CUDA 实际运算为准。参考 [vLLM 旧驱动支持](https://docs.vllm.ai/en/latest/deployment/docker/#running-on-systems-with-older-cuda-drivers)。如果仍报驱动不兼容，需管理员匹配 GPU 驱动；离线驱动安装依赖具体系统和内核，本包不自动修改它。
- **驱动较新 / CUDA 错误 803、804：**可将 `VLLM_ENABLE_CUDA_COMPATIBILITY=0` 后重新 gpu-check，使用宿主机原生驱动库；以实测成功的开关值启动。两种设置都失败时，再检查驱动与 Fabric Manager。
- **`nvidia-smi` 本身失败：**先修复宿主机驱动。这不是重新导入应用镜像能解决的问题。
- **`unresolvable CDI devices`：**重新运行 install-gpu-support；检查 `/etc/cdi/opencode-demo.yaml`。若复用了旧 Docker，需 Docker 28.3+ 的 CDI 支持，或由管理员为旧版启用 CDI；不要覆盖已有 Docker 数据。
- **8 卡 H100 SXM/NVSwitch 报 CUDA system not initialized：**让管理员检查 NVIDIA Fabric Manager 是否与驱动版本匹配、是否运行。单卡被 MIG 切分时不能假定它等于一张完整 H100。
- **OOM / KV cache 不够：**先确认所选 GPU 无其他任务，再降低 `MAX_MODEL_LEN`（应用 `VLLM_CONTEXT` 同步修改）或 `MAX_NUM_SEQS`；也可增加并行卡数。不要默认占用 8 张卡。
- **容器退出 137 / OOMKilled：**还要检查宿主机内存，模型加载会使用 CPU 内存；这与 GPU 显存不足是两类问题。让管理员检查系统日志和可用内存，再重试。
- **NCCL 报错/并行卡挂起：**检查所选 GPU、NVLink/NVSwitch、Fabric Manager。可临时选一张完整空闲 H100（同时把 GPU_IDS 和 TENSOR_PARALLEL_SIZE 改为单卡），在显存允许时排查；默认已配置 16 GiB 共享内存。
- **端口占用：**up 会检查并拒绝抢占端口。改 SERVE_PORT 后同步修改应用 VLLM_BASE_URL。
- **找不到 tokenizer / 缺分片：**补全模型目录及本地引用文件。模型离线变量已开启，缺文件会报错，不会到 Hugging Face 下载。
- **模型目录 Permission denied：**确认本地路径正确、目录和文件可读；开启 SELinux 的机器需管理员允许该目录被容器只读访问。脚本不会自动修改已有模型目录的安全标签，以免影响共用它的服务。
- **工具调用失败/只有思考没有答案：**启动脚本已配置 qwen3_coder 工具解析器、qwen3 推理解析器和关闭思考。先看 probe-model 的具体失败项，再检查权重是否对应原始 Qwen3.8-27B 格式、聊天模板是否完整。
- **安装/更新驱动或调整 MIG 后：**重新运行 install-gpu-support，再重建 vLLM 容器。systemd 环境中本包会在开机时刷新 CDI；没有 systemd 时每次开机手动运行该脚本。

日常命令：

```bash
sudo bash vllm.sh status
sudo bash vllm.sh logs
sudo bash vllm.sh stop
sudo bash vllm.sh start
# 改配置后：
sudo bash vllm.sh down
sudo bash vllm.sh up
```

这些命令只管理 `opencode-demo-vllm` 容器。宿主机已有的其他 vLLM 服务不会被停止。正常运行时随 Docker 开机启动；手动 stop 后需手动 start。源码和版本来源记录在本目录 `IMAGE-MANIFEST.json`；镜像以固定版本和 SHA-256 导出。
