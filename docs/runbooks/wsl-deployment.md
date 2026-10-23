# Tailnet 部署与日常运行

项目目录：`/home/xjx/opencode-agent-demo`。入口：**http://100.64.0.17:18080**。本手册用于统一源码后的 WSL 实例；内网离线版本使用 `deploy/intranet/` 中对应交付手册。

## 进程和模型

| 组件 | 地址或服务 |
| --- | --- |
| 网页入口 | Windows Tailscale Serve `100.64.0.17:18080` → WSL `127.0.0.1:18080` |
| Tailnet 网关 | `719agent-demo-tailnet.service` |
| 后端 | `719agent-demo-backend.service`，`127.0.0.1:18000`，单 worker |
| OpenCode | `719agent-demo-opencode.service`，`127.0.0.1:14096`，固定 1.18.31 |
| 独立示例采样 | `719agent-demo-loop.service` |
| 模型网关 | `http://100.64.0.7:18083/v1`，已有鉴权网关 |
| vLLM | server8 `127.0.0.1:8102`，`Qwen3.8-27B`，GPU `1,2,3`，TP=1、PP=3，上下文 32768 |

2026-10-23 已通过只读远端查询核对模型网关与 GPU 对应关系。应用部署不启动或重启远端 vLLM，也不改变其他 GPU 服务。

当前 provider 在模型级 `options.chat_template_kwargs` 中明确设置 `enable_thinking=false`。`variants.readiness.max_tokens=512` 只用于结构化就绪探测，业务请求保留 8192 输出额度；已通过固定版本 OpenCode 的实际请求体验证。就绪探测总时限为 60 秒，超时后最多再用 5 秒取消自己的会话并报告 `MODEL_PROBE_TIMEOUT`，不会重发不确定的业务请求。若使用另一 provider 配置，应保留该 readiness variant。

## 私有配置与账号

实际配置为 `deploy/runtime/runtime.env` 和 `deploy/runtime/provider.vllm-gpu123.json`，权限为 0600，目录为 0700。配置模板分别是 `deploy/runtime.env.example` 与 `deploy/provider.vllm-gpu123.example.json`。`DEMO_MODEL_API_KEY` 由运行环境提供，provider 只保留环境变量引用。

`DEMO_ORIGIN=http://100.64.0.17:18080`，`DEMO_LOCAL_DEV=1` 用于本地后端及 Tailnet 入口的部署模式；所有业务用户仍需登录。`NO_PROXY`/`no_proxy` 包含 localhost、127.0.0.1、100.64.0.7、100.64.0.17。不要重新运行初始化脚本覆盖现有服务凭据。

从旧版本升级时，先备份并停止后端，再运行 `scripts/init_admin.py` 创建首位管理员；旧业务历史归属该管理员。自注册用户不能替代此步骤。本次升级生成的管理员登录信息仅保存在本机 `deploy/runtime/initial-admin.credentials`，不进入 Git 或测试报告。管理员可按平台现有账号功能管理成员。

## 启停和日志

```bash
cd /home/xjx/opencode-agent-demo
systemctl --user status 719agent-demo-{loop,backend,opencode,tailnet}.service
journalctl --user -u 719agent-demo-backend.service -n 60 --no-pager
curl http://127.0.0.1:18080/api/v1/health
```

修改配置后重启 backend 和 opencode；修改 unit 后先 `systemctl --user daemon-reload`。四个 unit 中的路径按当前 WSL 安装位置编写，换机器须同步调整。Windows 现有 Tailscale Serve 规则和原 5173 服务保留。

```bash
systemctl --user restart 719agent-demo-opencode.service 719agent-demo-backend.service 719agent-demo-tailnet.service
```

采样器与后端独立。停止采样后不回填缺失窗口，恢复后需积累连续 120 秒样本。`live=true` 只表示服务存活，完整就绪以 `/api/v1/health` 的 `ready=true` 为准。WSL 整体关机重启会终止当前终端，不能把应用服务重启等同于 Windows/WSL 整体重启验收。

## 测试

```bash
cd /home/xjx/opencode-agent-demo
export PATH=/home/xjx/.nvm/versions/node/v22.23.3/bin:$PATH
set -a
source deploy/runtime/runtime.env
set +a
bash scripts/check.sh
npm run test:browser --prefix apps/dashboard
.venv/bin/python scripts/probe_vllm.py --base-url http://100.64.0.7:18083/v1 --model Qwen3.8-27B
.venv/bin/python scripts/probe_agents_mcp.py
.venv/bin/python scripts/doctor.py --model-probe
.venv/bin/python scripts/smoke_real.py --base http://100.64.0.17:18080
.venv/bin/python scripts/smoke_office.py --base http://100.64.0.17:18080
.venv/bin/python scripts/smoke_st.py --base-url http://100.64.0.17:18080 --username admin --output var/verification/st
```

验收脚本交互读取账号密码，也可使用其规定的环境变量。不要把密码放在命令行参数或提交日志中。真实验收会使用模型并创建属于验收账号的任务。浏览器测试只证明界面与测试后端链路，不能替代真实模型验收。

WSL 上与采样器并行跑 Python 回归时，可把测试临时目录放入内存文件系统，避免大量测试数据库的同步写入拖慢每秒采样；正式运行数据仍位于 `var/backend/`。本次完整回归使用了这种方式：

```bash
test_tmp_dir=$(mktemp -d /dev/shm/opencode-tests.XXXXXX)
PYTEST_ADDOPTS="--basetemp=$test_tmp_dir" bash scripts/check.sh
```

磁盘繁忙或主机暂停仍可能产生真实采样缺口。出现 `TELEMETRY_WINDOW_NOT_READY` 时检查采样进程、磁盘延迟及最新数据时间，等待恢复连续窗口，不清空历史或补造样本。

## 备份与回退

数据库升级前停 backend 和 loop，备份完整 `var/backend/`、`deploy/runtime/` 及当前源码版本；完整状态目录包含 B 回路曲线、办公文件等，不能只备份数据库。需要 OpenCode 会话回退时另保留 `var/opencode/`。备份包含凭据和业务数据，保存在仓库外或已忽略目录，权限限制为当前用户。

回退时先停四个服务，恢复同一备份中的代码、数据库/文件及配置，再恢复对应依赖环境并启动服务。新版本已产生的数据单独保留，不直接混入旧数据库。不要只 checkout 旧代码却继续使用较新数据库。

本次同步的停服备份位于相邻交付目录 `../opencode-agent-demo-deploy/backups/tailnet-sync-20261023-*/`；该目录不会推送远端。各次验收的原始证据保存在 `var/verification/`，对外发布只提交脱敏结论。
