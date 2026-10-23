# 工作台布局排查与改造（2026-10-12）

## 修改前

- 外层文档可滚动；聊天 / Monitor 又以 `clamp(620px, …)` 限高。1440×900 下文档高度 998px，390×844 下 1086px，发送区域底部分别在 920px / 1013px。
- 品牌、大标题、横向导航、工作区标题重复占据纵向空间，列表像浮在页面里的卡片。
- 手机会话及运行记录变成横向条带，挤压消息阅读区；调优历史沿用同样的问题。
- 消息更新调用 `scrollIntoView`，会影响祖先滚动容器，且用户阅读历史时被带回底部。
- 中文输入法 Enter 选词也会触发发送；输入框手动拉高容易进一步挤压内容。

## 修改计划

1. 用全视口应用容器与贴边侧栏替换卡片式框架。导航、会话 / 运行历史共用侧栏；保留宽度调整及收起能力。
2. 主区使用紧凑标题、内容区和示例提示。聊天仅消息区滚动，输入区固定；Monitor 保留操作栏与独立的详情滚动。
3. 900px 以下用默认关闭的抽屉，支持遮罩、Escape、焦点管理及选中后关闭；移除手机横向记录条。
4. 用户处于底部时跟随新消息；向上阅读时保留位置并提供回到最新入口。处理输入法及多行输入。
5. 调优和目录使用明确的内容滚动区，窄屏通过纵向 / 选择器布局保留全部操作。
6. 在隔离 Fake 后端验证对话、检查、复核、调优、目录和技能管理，以及长内容、窄屏、低高度与键盘导航；预览构建验证后发布静态文件。
7. 沿用 WSL 与 Windows Tailscale Serve 链路，发布至 `http://100.64.0.17:18080`，核对远端读取的版本和健康状态。

后端 API、契约、模型、鉴权、工作流、数据和权限配置保持原实现。修改前源码及静态产物备份：`var/backups/layout-20261012/`。

## 设计参考

- [ChatGPT release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes)：侧栏组织、适应不同视口、发送过程滚动行为与输入区布局。
- [Claude 会话管理](https://support.claude.com/en/articles/8230524-delete-or-rename-a-conversation)：会话位于左侧栏，更多操作承载重命名和删除。
- [Claude 外观设置](https://support.claude.com/en/articles/8887527-customizing-your-appearance-settings)：侧栏可通过按钮展开与收起。

以上用于交互参考；工作台继续沿用 PID Control Lab 的深色侧栏、浅色阅读区和薄荷绿操作色。

## 实现与验收

- `workspace.tsx` / `workspace.css`：全视口容器、统一导航、可调整宽度的侧栏、手机抽屉、焦点管理、视口及安全区适配。
- `chat-scroll.ts` / `chat.css`：消息滚动跟随与阅读位置保留、回到最新、固定输入区、多行高度限制。消息发送保留原 API，Enter 选词不发送。
- `main.tsx` 保留原业务请求、轮询及状态判断；目录增加手机选择器，调优将历史横条改为记录选择器。
- 构建通过：`npm run build --prefix apps/dashboard -- --outDir dist-layout-preview`，包含 TypeScript 检查。
- 后端：111 项 pytest 通过（29.49s）；有 2 条既有依赖弃用提示。
- 浏览器：10 项原有业务回归全部通过；11 项布局场景完成验证。第一次完整运行 20/21 通过；修复抽屉关闭后焦点恢复早于 `inert` 移除的问题后，相关抽屉与 Monitor 布局复测 2/2 通过。
- 视口：1440×900、1024×768、390×844、320×568、844×390；六个工作区均无文档滚动和横向溢出，长草稿发送按钮保持可达。
- 长对话：桌面与手机均验证了上翻阅读时新回复不改动位置、切换工作区后恢复位置、回到最新及固定输入区。
- 交互：验证输入法选词、Shift+Enter、普通 Enter、搜索会话、抽屉 Tab/Escape/遮罩及焦点恢复、窄屏目录与技能表单、依赖未就绪时的历史入口。

浏览器测试通过 `DEMO_BROWSER_DIST=apps/dashboard/dist-layout-preview npm run test:browser --prefix apps/dashboard` 使用隔离 Fake 后端，正式网关在验收期间仍读取原静态文件。后端测试和这些浏览器测试均不触发正式模型工作流。

## 发布与回退

已发布至 `http://100.64.0.17:18080`。先复制带内容哈希的资源，再原子替换 `dist/index.html`；保留旧资源以兼容已打开的页面。未重启业务服务，未修改数据库、模型和权限配置。

从 tailnet 的 server9 核对入口页面 SHA-256 和全部静态资源，健康接口 `ready: true`，历史查询正常。WSL 四项服务均为 `active`；继续沿用 Windows Tailscale Serve 到 WSL 回环网关的现有链路。证据：`layout-deployment.json`、`layout-tailnet-verification.json` 与 `layout-browser-verification.json`。

如需回退界面，将 `var/backups/layout-20261012/dist/index.html` 复制为 `apps/dashboard/dist/.index.rollback.html`，再重命名覆盖 `apps/dashboard/dist/index.html`；旧资源仍在，业务服务无需重启。源码备份位于同目录的 `source/`。
