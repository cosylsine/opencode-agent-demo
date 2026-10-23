"""Program-owned preparation for the simulation Control workflow."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from packages.adapters.catalog import _frontmatter, _skill_directory
from packages.domain.core import DomainError

WORKFLOW_VERSION = "prepared-control-v1"
REQUIRED_SKILLS = ("pid-tuning", "control-safety")


def load_control_skills(root: Path) -> dict[str, dict[str, str]]:
    skills = {}
    try:
        for name in REQUIRED_SKILLS:
            directory = _skill_directory(root, name)
            path = directory / "SKILL.md"
            if directory.is_symlink() or path.is_symlink():
                raise ValueError("symlink")
            with path.open("rb") as stream:
                raw = stream.read(16385)
            fields, content = _frontmatter(raw.decode("utf-8"))
            if len(raw) > 16384 or fields.get("name") != name or not content.strip():
                raise ValueError("invalid skill")
            skills[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "content": content.strip()}
    except (OSError, UnicodeError, ValueError, DomainError):
        raise DomainError("CONTROL_SKILLS_UNAVAILABLE", "内置调优技能缺失或无效，尚未发起模型请求", 503) from None
    return skills


@dataclass(frozen=True)
class ControlWorkflow:
    receipt: str
    skills: dict[str, dict[str, str]] = field(default_factory=dict)
    # The bound authorized read is also the tool implementation. Test clients
    # invoke it to model MCP effects; OpenCode uses the actual MCP endpoint.
    context_reader: Callable[[], dict] | None = field(default=None, repr=False, compare=False)

    def system(self) -> str:
        return (
            "本轮两项技能已由后端校验并预加载，以下是完整正文，等同已完成技能加载。"
            "后端也已通过授权 MCP 完成 get_tuning_context 读取，完整结果在请求的 control_context 中。"
            "准备流程已完成，你只负责提出候选，不要再调用 skill 或 get_tuning_context。"
            "根据 control_context.search_feedback 核对综合分方向、可控分量和探索阶段。"
            "若含 early_abstain_review，这是同一轮唯一一次早停复核：结合未试验的单参数方向重新判断；"
            "可以提出辨识试验，无须保证改善；若仍无合理新候选可说明证据后放弃。"
            "必须把 control_context.context_receipt 原样填入 StructuredOutput。"
            "PROPOSE 时 abstain_kind 填 null；确实没有可信新候选而 ABSTAIN 时填 NO_CANDIDATE；"
            "读取失败、缺少数据或其他流程问题时填 WORKFLOW_ERROR，不能将流程问题当作没有候选。\n"
            + "\n\n".join(f"<preloaded_skill name={name} sha256={item['sha256']}>\n"
                            f"{item['content']}\n</preloaded_skill>"
                            for name, item in self.skills.items())
        )
