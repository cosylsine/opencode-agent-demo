# WSL 迁移状态

## 已部署

- 项目：`/home/xjx/opencode-agent-demo`。
- 新入口：<http://100.64.0.17:18080>，Windows Tailscale Serve 转发到 WSL 回环网关。
- WSL 四个业务 user services 已安装、启用并运行；业务状态从空目录初始化。现有记录来自本次验收。
- Python 3.13.2、Node 22.23.3、OpenCode 1.18.31；OpenCode 与远端原二进制 SHA-256 相同。
- 源码原始快照保存在 `var/migration/source-baseline.tar`，文件校验清单为 `wsl-source-manifest.json`。
- 模型网关已由本次 agent 部署在 `100.64.0.6:18084`，unit 为 `719agent-demo-model-gateway.service`，使用独立密钥。
- GPU 0、1 上模型仍为原 `127.0.0.1:8000` vLLM 进程，迁移未重启模型。原模型 PID 为 `1121917`。
- 新旧服务凭据独立；模型密钥仅存在两端私有运行文件，文档不包含密钥。
- LycheeRAG 服务、模型端口和 Windows `5173` 转发保留。

## 验收结果

| 检查 | 结果与证据 |
|---|---|
| Python、静态检查、契约与前端构建 | 111 tests passed；Ruff、类型检查及构建通过；见 `wsl-check.log` |
| 浏览器 | 10 tests passed；见 `wsl-browser.log` |
| 模型接口 | 模型列表、普通生成、JSON Schema、自动和指定工具调用全部通过；无密钥及错误密钥均为 401 |
| 模型流式响应 | SSE 正常结束；见 `wsl-model-stream.json` |
| OpenCode 与 MCP | 固定版本、Agent 权限、四工具及无效凭据拒绝验证通过；见 `d0-probe.json`、`agent-mcp-probe.json` |
| 真实业务 | normal → NO_TUNING_REQUIRED；improvable → MANUAL_REVIEW_REQUIRED，不自动调用 Control；B 回路真实候选完成四工况评测；见 `real-integration.json` |
| B 回路验收后处理 | smoke 脚本在取得候选评测证据后请求取消剩余调优，历史保留 |
| 另一台 tailnet 主机浏览器 | 从 server9 打开新页面，3 Agent、3 Skill、无页面错误；见 `wsl-tailnet-browser.json` |
| 网络边界与浏览器写接口 | 内部路径 404、18000/14096 不可远程连接、错误 Origin/CSRF 返回 403、正确写入与清理成功；见 `wsl-tailnet-boundaries.json` |
| 服务重启及实际数据备份恢复 | 后端重启后两条 Monitor 历史一致，备份恢复哈希通过；网关保持运行；见 `local-deployment.json` |
| OpenCode 重启 | 本地健康恢复，并重新进行真实模型 doctor 探针；见 `wsl-doctor.json` |
| WSL 整体重启 | **尚未执行**，不可用服务重启代替此项 |
| 远端旧业务停用 | **尚未执行**，等待整体重启验收通过 |

单元测试使用 `/dev/shm/opencode-tests` 临时目录，以避免测试密集 fsync 拖慢 WSL 磁盘；实际业务一直使用项目下的 Linux 文件系统。两项依赖真实时钟的测试已改为在数据库启动后构造样本窗口，且只在构造合成样本时省略同步刷盘；实际采样仍执行 fsync。`local-deployment.json` 中的 `model_ready` 是后端刚启动时的瞬时快照，不是最终模型验收结论。

## 迁移中修复

- 将启动器和探针的远端 OpenCode 路径改为 `OPENCODE_BIN`。
- 为模型探针加入 `DEMO_MODEL_API_KEY`，并设置两套代理绕过变量。
- 四个工况文件并发持久化，降低 WSL 上串行刷盘造成的采样缺秒；保留真实缺口与重新积累窗口规则，新增回归测试。
- 原浏览器测试依赖旧测试目录已有历史，且没有等待新会话完成创建。现在通过测试 API 准备历史并等待新会话选中，支持全新测试目录。
- 页面网关不再因后端停止而被 systemd 连带关闭，后端恢复后可继续服务。
- 安装 Chromium 所需的 libcups2、libxdamage1、libcairo2、libpango-1.0-0 及其依赖。

## 最后一步：整体重启与切换

当前 Codex 进程运行在本 WSL 中；直接重启会终止执行环境和 IDE 连接。本次会话保留远端旧业务作为回退，交付可在 Windows 独立运行的脚本。

保存 IDE 工作后，在 **Windows PowerShell** 执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu-20.04\home\xjx\opencode-agent-demo\deploy\verify-wsl-restart-and-cutover.ps1"
```

它会核查重启确实发生、服务自启、连续采样、历史保留、跨主机新入口与 LycheeRAG 入口，再停用远端旧四个业务服务。失败则不执行后续停用。脚本已通过 PowerShell 语法检查，Python verifier 已验证会拒绝将未重启的实例报告为重启成功。

完成后以 `wsl-restart-verification.json` 和 `wsl-cutover-complete.json` 为最新状态依据；这两个成功文件目前未生成。远端维护及回退步骤见 [协调文档](wsl-migration-coordination.md)。
