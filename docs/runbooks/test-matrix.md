# V1 验收矩阵

最新检查（2026-09-24）：77 项 Python 测试、4 项 Playwright 通过，Ruff / TypeScript / Vite 构建通过；本机真实模型的 reception 普通问答、关键词触发 Monitor 与既有双 Agent 示例链路已通过。外部 TLS 和真实推理中异常时序仍待验收。

记录规则：自动化 Fake 与真实 OpenCode 模型验收分开。命令和最新结果以 development_progress.md 为准。

| 能力 | 自动化证据 | 真实环境状态 |
| --- | --- | --- |
| 契约 / JSON Schema / 严格数值 / hash | test_domain.py、test_api.py | 已导出 |
| 121 点观测与 PID 同快照 | test_domain.py | Example 已实现 |
| 改善 / 恶化 / 不安全 / 固定无改善 | test_domain.py、test_workflow.py | 真实模型的可改进示例一轮达标；其余边界由确定性测试覆盖 |
| Monitor 冲突 / 低置信度 / Control ABSTAIN | test_workflow.py | Fake 与规则链路覆盖；真模型特定输出不保证每次触发 |
| 有限格式修复、候选重提、重复候选 Guard | test_domain.py、test_workflow.py | 本机模型遇到嵌套 PID 字符串，受限解码后通过严格契约 |
| 连续 3 次无改善 / 5 轮 / 总时限 | test_workflow.py、test_boundaries.py | 确定性代码通过 |
| 同键相同请求 / 冲突 / 忙时合并 | test_api.py | 本机 API 可用 |
| 取消优先 / 终态不可变 | test_workflow.py、test_boundaries.py | 真模型 abort 待验收 |
| 15 个已提交状态点重启 | test_boundaries.py | 实际模型中断待验收 |
| 文件已完成 DB 未登记 / 损坏 | test_boundaries.py、test_operations.py | manifest + receipt 关联 |
| 已接受不重复 / HTTP 超时找回不重发 | test_boundaries.py | 模拟读超时与固定 messageID 恢复通过；真实网络超时待验收 |
| 丢失 Session / 未知结果 | test_boundaries.py | Fake 注入；一次真实结果未知已保守转人工，不计成功 |
| 文件哈希 / 路径穿越 | test_domain.py、test_api.py | 受控引用解析 |
| Token 作用域 / 撤销 / 越权工具 / 额外 run 参数 | test_api.py、test_boundaries.py | stdio 握手、无效凭据拒绝与双角色实际只读 MCP 调用通过 |
| 替换 ObservationSource / CandidateEvaluator | test_adapter_contract.py | 第二组极简测试实现 |
| 未认证 / CSRF / degraded | test_api.py | 公网 Nginx/TLS 未配置 |
| 备份 / 恢复 / 损坏拒绝 | test_operations.py | 本机服务重启/备份/恢复已通过，见 local-deployment.json |
| 页面触发 / 曲线 / 建议 / 状态 | Playwright dashboard.spec.ts | **4 passed**：对话、快捷检查、控制台、重载恢复与手机端 390/320px 无横向溢出 |
| reception 对话 / 关键词 / 幂等 / 隔离 | test_api.py、Playwright dashboard.spec.ts | 本机模型普通文本回复和正式 API 关键词触发 `normal` run，Monitor 输出 `NORMAL_STABLE` |
| 三 Agent 实际加载、默认 deny、四工具连接 | probe_agents_mcp.py | **1.18.31 实测通过加载和 MCP 连接**；reception 无业务工具权限 |
| 真实两 Agent + MCP + 示例 | smoke_real.py | **通过**：`normal=NO_TUNING_REQUIRED`，`improvable=SUCCEEDED/TARGET_REACHED`，一轮候选已评测；见 real-integration.json |

未声称当前测试覆盖所有现场信号分类：规则首版只分类正常、偏高/偏低、质量异常和不确定；其他状态枚举保留，不引入分类模型。
