"""Compact, model-specific feedback for agent-owned offline experiments."""

from packages.contracts.tuning import TuningRun

from .evaluate import WEIGHTS
from .plant import PID_BOUNDS

INITIAL_CANDIDATES = 3
SEARCH_POLICY_VERSION = "simulation-search-v1"


def search_feedback(run: TuningRun) -> dict:
    best = next((trial for trial in run.trials if trial.trial_id == run.best_trial_id), None)
    baseline = next((trial for trial in run.trials if trial.iteration == 0), None)
    count = sum(trial.iteration > 0 for trial in run.trials)
    independent = ["pressure", "power"] if run.model_version in {
        "steam-cycle-b-v1", "steam-cycle-b-v2",
    } else []
    violations = [scenario for scenario, result in (baseline.result["scenarios"].items()
                  if baseline else []) if "pressure" in independent
                  and "STEAM_PRESSURE" in result["violations"]]
    center = run.best_pid or run.baseline_pid
    seen = [trial.pid for trial in run.trials]
    seen.extend(item["agent_output"]["candidate_pid"] for item in run.rejections
                if (item.get("agent_output") or {}).get("candidate_pid"))
    probes = []
    for key, floor in (("kp", 0.1), ("ki", 0.0025), ("kd", 0.1)):
        step = max(abs(center[key]) * 0.5, floor)
        for direction in (-1, 1):
            candidate = center.copy()
            low, high = PID_BOUNDS[key]
            candidate[key] = round(min(high, max(low, center[key] + direction * step)), 6)
            if abs(candidate[key] - center[key]) <= 1e-6 or any(
                all(abs(candidate[k] - old[k]) <= 1e-6 for k in PID_BOUNDS) for old in seen
            ):
                continue
            probes.append({"parameter": key, "pid": candidate})
    progress = None
    if best and baseline:
        improvement = baseline.result["score"] - best.result["score"]
        progress = {
            "baseline_safe": baseline.result["safe"], "best_safe": best.result["safe"],
            "baseline_score": baseline.result["score"], "best_score": best.result["score"],
            "score_reduction": improvement,
            "relative_reduction": improvement / baseline.result["score"] if baseline.result["score"] else None,
        }
    return {
        "policy_version": SEARCH_POLICY_VERSION,
        "score_direction": "LOWER_IS_BETTER",
        "weights": WEIGHTS,
        "aggregation": "0.7 × 四工况平均分 + 0.3 × 最差工况分；先通过全部安全边界",
        "baseline_comparison": progress,
        "evaluated_candidates": count,
        "phase": "INITIAL_EXPLORATION" if count < INITIAL_CANDIDATES else "LOCAL_REFINEMENT",
        "recommended_initial_candidates": INITIAL_CANDIDATES,
        "pid_independent_components": independent,
        "uncontrollable_pressure_violation_scenarios": violations,
        "model_note": "本简化模型的汽轮机/燃烧子系统不接收给水流量反馈，压力和功率不随泵 PID 改变。"
                      "它们仍计分且压力仍须满足安全边界；方向判断着重 flow、level、pump_motion 的权衡。"
                      "各分量不必同时下降，以安全综合分为准。" if independent else None,
        "experiment_policy": "候选无须预先保证改善。初期样本不足或分量方向不一致时，可只改变一个参数做辨识试验；"
                             "失败试验也提供信息。下列点仅为未试验的有界单参数建议，不是已验证的改善。"
                             "由 Agent 选择或另提候选，程序不代替 Agent 生成试验。"
                             "不可控压力越界、确无合理新候选或预算结束时可停止。",
        "untried_single_parameter_probes": probes,
        "early_abstain_review": {
            "iteration": run.search_reviews[-1]["iteration"],
            "reason": run.search_reviews[-1]["reason"][:500],
        } if run.search_reviews and run.search_reviews[-1]["iteration"] == run.pending_iteration else None,
    }
