# 2026-10-21 累计更新交付验收

适用基线：已有内网服务已修好 403 与 Chrome 86；不需要安装其他中间版本。本次交付为 `opencode-agent-demo:20261021-intranet-amd64`，数据库最新 `0007`。未连接或修改所内服务器；制作机原有预览和其他项目服务保留。

## 已完成的检查

| 项目 | 结果 / 证据 |
| --- | --- |
| 全部 Python 回归 | **365 passed**，覆盖 API、授权/CSRF、平台资源、办公文件、MCP、迁移、备份恢复、B 回路仿真/老化/搜索/上下文、ST 校验/扫描周期等；`pytest-final.txt`。 |
| 交付镜像内部 Python 回归 | 在最终镜像内、`--network none`、只读测试源码及临时数据环境，**同一组 365 passed**；`pytest-image.txt`。两次运行不是 730 个不同用例。 |
| 静态检查/前端构建 | 全部 source Ruff 通过；TypeScript + Vite 构建通过，仍以 Chrome 86 为 JS/CSS 目标；`ruff.txt`、`frontend-build.txt`。 |
| 现代 Chromium | Chromium 145，**38 passed**，含登录/注册、资源生命周期、办公工具、上传/下载、通用助手、Monitor/B 回路/ST、桌面/手机布局；`browser-modern-verified.txt`。 |
| 真正 Chromium 86 | **Chromium 86.0.4240.0，38 passed**，执行同一套界面用例。使用实际旧内核，未仅以 UA 或删除 API 冒充旧浏览器；`browser-chromium86-verified.txt`。 |
| 额外旧 API 缺失检查 | 上述 38 项中含原生 API 和移除 at/hasOwn/structuredClone/randomUUID 的两种场景；实际登录、Markdown 强调、多轮、刷新、六工作区切换，无 pageerror、无外部请求。 |
| 真实 TCP 来源/403 回归 | 独立 `--network none` + NET_ADMIN 容器内配置服务器 10.70.100.220:3000、允许客户端 10.70.42.5、拒绝客户端 10.70.128.1。真实登录、静态资源和合法写入通过；恶意 Origin、错误 CSRF、内网访问 internal、伪造转发来源均拒绝。宿主机网卡/防火墙未修改；`lan-tcp-final.txt`。 |
| 断网镜像构建 | `--pull=false --network none`，所有依赖来自本地办公 runtime；`image-build-final.txt`。 |
| 镜像/源码一致性 | **134 个应用、部署、技能和前端文件摘要完全相同**；镜像不存在运行密钥及测试后端；`image-source-check.json`。 |
| 正式镜像归档 | docker save 的两端退出码、tar 清单、配置摘要、两镜像标签已核对；正式归档实际 `deploy.sh load` 成功；`archive-load.txt`、`images/IMAGE-MANIFEST.json`。 |
| 隔离容器完整就绪 | 最终镜像内实际后端/OpenCode/MCP/采样，`check` **ready=true**，数据库返回实际版本 0007；`rehearsal-check.txt`。模型是独立挂载的 TEST 协议服务。 |
| 办公闭环 | 真实后端/OpenCode/办公 MCP/文件工具，两轮 Excel→核对汇总→Word，下载并重开文件核对；`rehearsal-office.txt`。模型输出来自 TEST 协议服务，不代表真实 Qwen 质量。 |
| 升级及整套回退 | 用已保存的 403/Chrome 修复版真实镜像创建 **0005 数据库**和旧会话，经交付 Bash 脚本完成保存旧镜像、停服备份、安装、管理员初始化、0007 迁移和新镜像启动。旧会话归管理员，现场配置/密钥摘要不变，自定义文件保留，旧源码残留被替换。回退后数据库/配置/密钥/deploy.sh 摘要逐项等于升级前，旧镜像可再次启动；`migration-rollback.json`。 |
| 误操作和异常路径 | 重复 apply 被拒绝且不停止运行服务；额外模拟 deploy.sh 缺失的部分安装状态仍可使用盘内回退脚本恢复，见迁移记录。 |
| 手册 | 两个部署 Shell 脚本及手册 15 个 Bash 命令块语法通过；手册覆盖复制/校验、预检、空间、停服、迁移、启动、403/旧浏览器、文件、模型、回退和验收标准。 |
| 中间盘清理 | 已删除被替代的四个 delivery 目录，释放 1,403,037,147 字节（约 1.31 GiB）；保留原备份、业务数据、模型和历史证据，旧盘清单另存；`cleanup.json`。 |

## 本轮发现并修复

1. ST 操作选中样式使用 `:has(input:checked)`，Chrome 86 不支持。改用随勾选状态同步的 `is-selected` 类，保持选中视觉反馈。
2. 健康接口固定显示数据库 0006，实际 ST 迁移已到 0007。改为读 Alembic 实际 revision，并测试实际 revision 变化会反映在健康接口。
3. 旧 `chrome86_browser.cjs` 仍针对已经撤下的接待首页。更新为当前账号/办公工作区回归入口，可通过 CHROME86_BIN 指定真实旧内核；新增无外部请求/Markdown/多轮用例。
4. 新版 Playwright 的拖动拦截/下载事件在旧版 CDP 中不存在。测试驱动在 Chromium 86 下使用原生 `Input.dispatchMouseEvent` 和 `Page.setDownloadBehavior`，真实点击后确认文件落盘、文件名正确且非空；现代浏览器继续原生 Playwright 下载检查。没有跳过拖动或下载业务断言。
5. 制作完整累计包并以独立 `RELEASE-SHA256SUMS` 校验，避免用旧阶段清单漏打 ST/办公等后续文件。更新脚本不覆盖现场配置，在停服备份后替换完整代码树，避免遗留旧资源。旧刻盘脚本已明确停止生成过时版本。

首次测试从仓库外层启动，导致业务测试子进程找不到 examples 模块；从项目规定的 source 目录运行后通过。测试中间的旧 CDP 驱动错误、定位器严格模式错误和服务尚未就绪时的 503 均已定位并复测。`pytest-final.txt`、`pytest-image.txt`、`*-verified.txt` 等是最终结果；其他本地日志为排查过程，未作为通过证据。

## 浏览器来源与复跑

Chromium 86 使用 Chromium 官方 snapshot `Linux_x64/800071`，实际执行 `chrome --version` 输出 `Chromium 86.0.4240.0`。版本对应关系可参阅 [Puppeteer 官方兼容表](https://pptr.dev/chromium-support/) 与 [v5.3.0 revision 定义](https://github.com/puppeteer/puppeteer/blob/v5.3.0/src/revisions.ts)。浏览器和为本机解压的测试运行库放在制作机缓存中，不进入光盘或生产镜像。

制作机已有依赖时可复跑：

```bash
cd source
../.dev-venv/bin/python -m pytest -q tests ../verification/test_lan_runtime.py ../verification/test_mcp_config.py
cd ..
npm run build --prefix source/apps/dashboard
npm run test:browser --prefix source/apps/dashboard
CHROME86_BIN=/实际路径/chrome npm run test:browser --prefix source/apps/dashboard
```

实际旧内核可能需要相应共享库，按制作机缓存设置 LD_LIBRARY_PATH；本次没有向现场环境安装浏览器或修改操作系统。测试数据使用独立临时目录；已有浏览器测试服务占用 18001 时先确认归属再结束测试服务。

## 边界与现场仍须执行

本轮全链路容器测试使用确定性 TEST 模型服务；未重新调用现场 Qwen/GPU，也没有把协议测试描述为真实模型推理成功。历史真实模型开发验证保留在旧 verification 记录中。现场必须执行手册中的 probe-model、probe-agent、smoke-office、smoke，以及原 Windows 360 极速内核上的操作验收。ST 产物的“待工程验证”不等于目标 PLC 验证通过。

本机生成并校验了刻盘目录；没有实体刻录设备参与本轮验证。实体光盘刻录后的读回 SHA-256 校验仍需用户执行。回退会恢复升级前数据快照，升级后新数据保留在 failed-state 目录但不会自动并回旧库。
