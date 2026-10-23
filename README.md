# 部门智能体平台

统一产品源码仓库，维护 Tailnet 与内网两种部署。当前功能基线来自已交付的 `INTRANET_20261021`，包含账号与权限、私有/公共 Agent 和 Skill、17 项办公工具、附件与产物、Monitor、B 回路仿真调优和 ST 代码优化。

Tailnet 入口为 **http://100.64.0.17:18080**。模型接入 **http://100.64.0.7:18083/v1**，模型 ID 为 **Qwen3.8-27B**，对应 server8 的 GPU 1、2、3。模型服务与应用分开维护；本仓库不包含模型权重或实际凭据。

## 运行与开发

Python 3.13.2、Node 22.23.3、OpenCode 1.18.31。Python 依赖由 `requirements.lock` 固定，前端依赖由 `apps/dashboard/package-lock.json` 固定。源码契约统一保存在 `packages/contracts`，前端类型从导出的 OpenAPI 生成。

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
npm ci --prefix apps/dashboard
.venv/bin/python scripts/export_contracts.py
npm run generate --prefix apps/dashboard
npm run build --prefix apps/dashboard
```

首次安装可运行 `.venv/bin/python scripts/init_runtime.py` 创建私有配置，再根据 `deploy/runtime.env.example` 配置服务；已有部署保留原配置和凭据。模型模板为 `deploy/provider.vllm-gpu123.example.json`，实际 provider 文件放入已忽略的 `deploy/runtime/`。初次创建管理员需在后端停止时执行 `.venv/bin/python scripts/init_admin.py`；不要把首个自注册用户当作管理员。

当前 WSL 服务、模型接入、账号、日志、验收与回退见 [Tailnet 运行手册](docs/runbooks/wsl-deployment.md)。后端仅使用一个 worker；OpenCode、后端和模型内部接口不对外直接开放。

## 检查

```bash
bash scripts/check.sh
npm run test:browser --prefix apps/dashboard
```

浏览器测试使用独立 `18001` 后端和 `var/browser-tests` 数据，明确使用测试模型。首次需要安装 Playwright Chromium。WSL 上可将 Python 测试临时目录置于 `/dev/shm`，正式数据库仍放在本地磁盘。

真实模型验收使用 `scripts/probe_vllm.py`、`scripts/probe_agents_mcp.py`、`scripts/smoke_real.py`、`scripts/smoke_office.py` 和 `scripts/smoke_st.py`；运行前加载私有配置并准备验收账号。真实验收会创建任务并使用模型推理资源，结果保存到本地 `var/verification/`，不提交包含业务内容的原始日志。

## 功能边界

Monitor 读取持续运行的示例回路。B 回路由真实 Control Agent 提出参数候选，平台逐工况仿真并裁决。ST 功能生成审查、注释、修复与测试草稿，执行结构和受限扫描逻辑检查；尚未调用目标 PLC 编译器或执行目标 PLC 测试。平台没有真实设备参数下发能力。

办公资源按账号隔离，公共资源与发布版本受权限控制。登录 Cookie、CSRF、附件上传和下载经 Tailnet 网关转发；网关不向不同用户共享上游会话。

## 版本与内网交付

- `main`：统一产品开发与 Tailnet 验证。
- `INTRANET_20261021`：经实际交付清单核对的内网源码基线，保留原始代码和部署模板。
- `deploy/intranet/`：内网 Docker、安装、升级、回退、刻盘脚本、配置模板及资产来源。
- `docs/releases/INTRANET_20261021.json`：原始交付文件摘要及入库范围。
- 本地 `../opencode-agent-demo-deploy/`：镜像、离线依赖、源码快照、更新盘和验收材料，不作为第二套产品主线。

内网维护及指定版本导出见 [内网交付维护说明](deploy/intranet/MAINTAINING.md)。Git 中保存重建所需的维护资产；大型依赖镜像和运行库仍需按清单另行归档。源码导出不等于完整离线安装包，历史 20261021 刻盘脚本也不能直接用于制作新的发布版本。

实际配置、凭据、数据库、用户产物、模型、镜像和备份均不进入 Git。内网修复回流主线，再按新版本制作交付包。`docs/runbooks/` 中较早的记录保留为历史说明，机器相关附件存于原本地交付归档；当前操作以 Tailnet 运行手册及所选内网版本的交付手册为准。
