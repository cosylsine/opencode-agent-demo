# WSL 部署与日常运行

项目目录：`/home/xjx/opencode-agent-demo`。入口：<http://100.64.0.17:18080>。

本部署使用 Python 3.13.2、Node 22.23.3、OpenCode 1.18.31。源码基线见 `wsl-source-manifest.json`；OpenCode SHA-256 为 `f9dab32248695e9ebd56b16a1921798fd85112cf5a69c7dfd0cabc1e17be4a11`。旧 README 中 server9 的安装步骤仅用于理解原部署，以本文为 WSL 操作依据。

## 服务

```bash
systemctl --user status 719agent-demo-{loop,backend,opencode,tailnet}.service
journalctl --user -u 719agent-demo-backend.service -n 60 --no-pager
curl --noproxy '*' http://127.0.0.1:18080/api/v1/health
```

四个 user units 随 WSL 的 systemd 用户管理器启动，用户已启用 linger。Windows Tailscale Serve 在后台持久转发 `18080`，原 `5173` 规则保留。该配置不会在 Windows 重启后主动拉起 WSL；需先启动 `Ubuntu-20.04` 发行版。

后端只能使用单 worker，不启用 reload。示例采样器独立运行，停机后不补填历史窗口；启动后等待连续 120 秒样本。

## 配置

私有运行文件为 `deploy/runtime/runtime.env`，provider 为 `deploy/runtime/provider.vllm-gpu01.json`，均为 0600。不要运行初始化脚本覆盖现有凭据。

- `OPENCODE_BIN`：`/home/xjx/opencode-agent-demo/bin/opencode`。
- `DEMO_STATE_DIR`：`/home/xjx/opencode-agent-demo/var/backend`。
- `DEMO_ORIGIN`：`http://100.64.0.17:18080`。
- provider：`local-vllm`，模型：`qwen3.6-35b-a3b`，baseURL：`http://100.64.0.6:18084/v1`。
- `DEMO_MODEL_API_KEY` 供 provider 的 `{env:DEMO_MODEL_API_KEY}` 及模型探针读取。
- `NO_PROXY` 和 `no_proxy` 包含 localhost、127.0.0.1、100.64.0.6、100.64.0.17。

OpenCode 与 MCP 的凭据为 WSL 新生成；不复用远端旧实例凭据。本地仅初始化新业务状态，不迁移历史曲线、Session、缓存和日志。

修改配置后重启后端及 OpenCode；修改 unit 后先 `systemctl --user daemon-reload`。

## 验证

```bash
cd /home/xjx/opencode-agent-demo
export PATH=/home/xjx/.nvm/versions/node/v22.23.3/bin:$PATH
set -a
source deploy/runtime/runtime.env
set +a
bash scripts/check.sh
.venv/bin/python scripts/probe_vllm.py --base-url http://100.64.0.6:18084/v1
.venv/bin/python scripts/probe_opencode.py
.venv/bin/python scripts/probe_agents_mcp.py
.venv/bin/python scripts/doctor.py --model-probe
npm run test:browser --prefix apps/dashboard
.venv/bin/python scripts/smoke_real.py --base http://100.64.0.17:18080
```

最后一项会产生真实诊断和 B 回路仿真记录、消耗模型推理资源。测试通过不意味着模型能对真实设备下发参数，本项目仍仅提供演示与仿真建议。

远端网关及旧实例回退见 [迁移协调文档](wsl-migration-coordination.md)。

WSL 磁盘的同步写延迟较高，完整单元测试可使用 `mkdir -p /dev/shm/opencode-tests` 后执行 `TMPDIR=/dev/shm/opencode-tests bash scripts/check.sh`。这只改变测试临时目录；业务数据库与采样数据仍保存在项目的 Linux 文件系统中。真实服务的重启及备份恢复另外在实际业务目录验证。

采样器对四个独立工况文件并发执行持久化写入，避免四次 fsync 的延迟串行累积；全部完成后才继续，写失败仍上报，不补填停机数据。主机暂停或磁盘持续拥塞导致真实采样缺秒时，仍需重新积累连续窗口。

网关独立于后端运行：后端停止时网关返回 502，后端恢复后自动继续代理。它不再因后端停止而被 systemd 连带关闭。

## 完成整体重启验收与切换

当前 Codex/SSH 会话也运行在此 WSL 内，整体重启会将其结束。因此整体重启由以下 Windows 脚本完成；在保存 IDE 工作后，从 **Windows PowerShell** 运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu-20.04\home\xjx\opencode-agent-demo\deploy\verify-wsl-restart-and-cutover.ps1"
```

脚本记录业务历史基线、重启 `Ubuntu-20.04`、等待最多 5 分钟检查模型与连续采样窗口、核对历史未丢失，再从 server9 验证新入口和原 LycheeRAG 入口。全部通过才停止并禁用远端旧四个业务服务；远端模型与新模型网关保留。

失败会停止后续切换，不删除任何业务数据。成功结果为 `wsl-restart-verification.json` 和 `wsl-cutover-complete.json`。脚本在实际执行前仅做语法及非破坏性检查，不把服务重启误记为 WSL 整体重启。
