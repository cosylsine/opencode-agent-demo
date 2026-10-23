"""Read the configured OpenCode catalog and register bounded project skills."""

import ast
import hashlib
import json
import re
import shutil
import unicodedata
from pathlib import Path

from packages.contracts.api import (
    CatalogAgent,
    CatalogResponse,
    CatalogSkill,
    CatalogTool,
    SkillRegistration,
)
from packages.domain.authorization import TOOLS
from packages.domain.core import DomainError

TEMPLATE = """---
name: your-skill-name
description: 一句话说明这个技能解决什么问题，以及何时使用。
---

# 技能名称

## 触发词

- 触发词一
- 触发词二

## 执行说明

写下 Agent 在触发后应遵循的步骤或注意事项。
"""
PROTECTED_SKILLS = {"control-safety", "pid-tuning", "steam-system-monitoring"}
SKILL_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z")


def skill_name(title: str) -> str:
    title = title.strip()
    if not title or len(title) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in title):
        raise DomainError("INVALID_SKILL", "请填写有效的技能名称", 422)
    ascii_name = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:64].strip("-")
    return slug or "skill-" + hashlib.sha256(title.encode()).hexdigest()[:10]


def _skill_directory(root: Path, name: str, deleted: bool = False) -> Path:
    if not SKILL_NAME.fullmatch(name):
        raise DomainError("INVALID_SKILL", "技能标识无效", 422)
    opencode_dir = root.resolve() / ".opencode"
    base = opencode_dir / ("deleted-skills" if deleted else "skills")
    if opencode_dir.is_symlink() or base.is_symlink():
        raise DomainError("INVALID_SKILL", "技能目录配置无效", 422)
    return base / name


def _skill_entry(path: Path, agents: list[CatalogAgent], deleted: bool = False) -> CatalogSkill | None:
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        return None
    fields, body = _frontmatter(path.read_text(encoding="utf-8"))
    name = path.parent.name
    if fields.get("name", name) != name:
        return None
    trigger_section = re.search(r"^## 触发词\s*\n((?:- .+\n?)+)", body, re.M)
    triggers = re.findall(r"^- (.+)$", trigger_section.group(1), re.M) if trigger_section else []
    return CatalogSkill(name=name, title=_first_heading(body, name),
                        description=fields.get("description", ""), triggers=triggers,
                        agents=[] if deleted else [a.name for a in agents if name in a.skills],
                        registered="## 触发词" in body, deleted=deleted,
                        deletable=name not in PROTECTED_SKILLS and bool(SKILL_NAME.fullmatch(name)))


def _frontmatter(content: str) -> tuple[dict[str, str], str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", content, re.S)
    if not match:
        return {}, content
    fields = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            value = value.strip()
            try:
                fields[key.strip()] = json.loads(value) if value.startswith('"') else value.strip("'")
            except json.JSONDecodeError:
                fields[key.strip()] = value
    return fields, match.group(2)


def _first_heading(body: str, fallback: str) -> str:
    match = re.search(r"^# (.+)$", body, re.M)
    return match.group(1).strip() if match else fallback


def _tool_docs(root: Path) -> dict[str, str]:
    source = root / "apps" / "mcp_server.py"
    if not source.is_file():
        return {}
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return {node.name: ast.get_docstring(node) or "" for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and
            any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and
                d.func.attr == "tool" for d in node.decorator_list)}


def read_catalog(root: Path) -> CatalogResponse:
    root = root.resolve()
    agents_dir = root / ".opencode" / "agents"
    skills_dir = root / ".opencode" / "skills"
    deleted_dir = root / ".opencode" / "deleted-skills"
    if (root / ".opencode").is_symlink() or skills_dir.is_symlink() or deleted_dir.is_symlink():
        raise DomainError("INVALID_SKILL", "技能目录配置无效", 422)
    agents: list[CatalogAgent] = []
    for path in sorted(agents_dir.glob("*.md")):
        if path.is_symlink():
            continue
        fields, body = _frontmatter(path.read_text(encoding="utf-8"))
        skill_names = re.findall(r"^    ([\w-]+): allow\s*$", path.read_text(encoding="utf-8"), re.M)
        tool_names = re.findall(r"^  industrial_control_([\w-]+): allow\s*$", path.read_text(encoding="utf-8"), re.M)
        agents.append(CatalogAgent(name=path.stem, description=fields.get("description") or body.strip().splitlines()[0],
                                   mode=fields.get("mode", "primary"), skills=skill_names, tools=tool_names))
    skills = [entry for path in sorted(skills_dir.glob("*/SKILL.md"))
              if (entry := _skill_entry(path, agents)) is not None]
    deleted_skills = [entry for path in sorted(deleted_dir.glob("*/SKILL.md"))
                      if (entry := _skill_entry(path, agents, deleted=True)) is not None]
    docs = _tool_docs(root)
    tools = [CatalogTool(name=name, description=docs.get(name, "只读工具"),
                         agents=[a.name for a in agents if name in a.tools])
             for name in sorted(set(docs) | set().union(*TOOLS.values()))]
    return CatalogResponse(schema_version="1.0", agents=agents, skills=skills,
                           deleted_skills=deleted_skills, tools=tools, template=TEMPLATE)


def register_skill(root: Path, request: SkillRegistration) -> CatalogSkill:
    title = request.title.strip()
    description = request.description.strip()
    triggers = list(dict.fromkeys(value.strip() for value in request.triggers if value.strip()))
    instructions = request.instructions.strip()
    if not title or not description or not triggers or any(
        any(ord(char) < 32 or ord(char) == 127 for char in value)
        for value in [title, description, *triggers]
    ):
        raise DomainError("INVALID_SKILL", "请填写名称、描述和至少一个触发词，且每项保持单行", 422)
    if any(value in {"触发词一", "触发词二"} for value in triggers):
        raise DomainError("INVALID_SKILL", "请把模板中的示例触发词改成实际用语", 422)
    slug = skill_name(title)
    if slug in {"control-safety", "pid-tuning", "steam-system-monitoring"}:
        raise DomainError("SKILL_EXISTS", "同名技能已存在，请更换名称", 409)
    # Generated frontmatter stays one line and the directory name stays within skills/.
    summary = description + f" 触发词：{'、'.join(triggers)}。"
    content = (f"---\nname: {slug}\ndescription: {json.dumps(summary, ensure_ascii=False)}\n---\n\n"
               f"# {title}\n\n## 触发词\n\n" + "".join(f"- {word}\n" for word in triggers) +
               "\n## 执行说明\n\n" + (instructions or f"当请求符合上述触发词时，围绕“{description}”提供清晰、准确的帮助。") + "\n")
    skills_root = _skill_directory(root, slug).parent
    skills_root.mkdir(parents=True, exist_ok=True)
    directory = skills_root / slug
    if _skill_directory(root, slug, deleted=True).exists():
        raise DomainError("SKILL_ARCHIVED", "同名技能在已删除列表中，请先恢复该技能", 409)
    try:
        directory.mkdir()
        with (directory / "SKILL.md").open("x", encoding="utf-8") as file:
            file.write(content)
    except FileExistsError as exc:
        raise DomainError("SKILL_EXISTS", "同名技能已存在，请更换名称", 409) from exc
    except OSError:
        if directory.exists() and not (directory / "SKILL.md").exists():
            directory.rmdir()
        raise
    return CatalogSkill(name=slug, title=title, description=summary,
                        triggers=triggers, agents=[], registered=True, deletable=True)


def move_skill(root: Path, name: str, restore: bool = False) -> CatalogSkill:
    if name in PROTECTED_SKILLS and not restore:
        raise DomainError("SKILL_PROTECTED", "工作流内置技能不可删除", 403)
    source = _skill_directory(root, name, deleted=restore)
    destination = _skill_directory(root, name, deleted=not restore)
    if source.is_symlink() or not source.is_dir():
        raise DomainError("SKILL_NOT_FOUND", "技能不存在或状态已变化，请刷新列表", 404)
    entry = _skill_entry(source / "SKILL.md", [], deleted=restore)
    if entry is None:
        raise DomainError("INVALID_SKILL", "技能文件无效，无法操作", 422)
    if destination.exists() or destination.is_symlink():
        raise DomainError("SKILL_EXISTS", "目标位置已有同名技能，无法操作", 409)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    return entry.model_copy(update={"deleted": not restore, "agents": []})


def permanently_delete_skill(root: Path, name: str) -> CatalogSkill:
    if name in PROTECTED_SKILLS:
        raise DomainError("SKILL_PROTECTED", "工作流内置技能不可删除", 403)
    directory = _skill_directory(root, name, deleted=True)
    if directory.is_symlink() or not directory.is_dir():
        raise DomainError("SKILL_NOT_FOUND", "已删除技能不存在，请刷新列表", 404)
    entry = _skill_entry(directory / "SKILL.md", [], deleted=True)
    if entry is None:
        raise DomainError("INVALID_SKILL", "技能文件无效，无法操作", 422)
    shutil.rmtree(directory)
    return entry
