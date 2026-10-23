---
name: steam-system-monitoring
description: Interpret frozen EXAMPLE observation quality, signed error and deterministic metrics for Monitor.
---
请求声明 skills_preloaded 且提供 monitor_context 时，后端已校验并加载本技能和 control-safety，且通过本轮授权 MCP 完成三个只读工具的读取。直接使用 monitor_context.get_recent_window、monitor_context.calculate_metrics、monitor_context.detect_state；不要重复加载技能或读取工具。未预加载时须各完成一次技能加载和三个 MCP 读取。缺失或读取失败不能用猜测替代。

版本 2.0。单位为 demo_unit。质量有效且规则为 STEADY_HIGH（有符号稳态误差大于 +0.08）或 STEADY_LOW（小于 -0.08）时，明确建议调参：needs_tuning=true，manual_review_required=false。指标超过目标值本身不能触发人工复核。NORMAL_STABLE 需要稳态误差不超过 0.08、超调不超过 0.02、末段标准差不超过 0.02、调节时间不超过 40 秒；此时 needs_tuning=false、manual_review_required=false。缺失或 BAD 样本、规则冲突、证据不足才需要人工复核。持续稳态偏差会让按绝对目标带计算的 settling_time_s 变长，不能单独解释为控制动态过慢。自报置信度不是校准概率。不要掩盖数据缺陷或声称采集自真实设备；解释字段使用简体中文。
