"""Ownership, immutable releases and dependency checks for user-authored assistants."""

import hashlib

from sqlalchemy import or_, select

from packages.adapters.platform_store import executions, resources, tool_settings, versions
from packages.contracts.common import now, uid
from packages.contracts.office import AgentContent, ResourceInfo, SkillContent
from packages.domain.core import DomainError

GENERAL_RESOURCES = {"builtin-agent-general-assistant", "builtin-skill-general-assistant"}


def skill_markdown(content):
    return f"# {content['title']}\n\n{content['description']}\n\n" + "\n\n".join(
        f"## {label}\n\n{content.get(key) or '未额外指定'}"
        for key, label in (
            ("applicability", "适用条件"),
            ("inputs", "输入要求"),
            ("instructions", "执行步骤"),
            ("exceptions", "异常处理"),
            ("output_format", "输出要求"),
        )
    )


class ResourceService:
    def __init__(self, platform):
        self.p = platform

    def exists(self, resource_id):
        with self.p.engine.connect() as c:
            return (
                c.execute(select(resources.c.id).where(resources.c.id == resource_id)).first()
                is not None
            )

    def deleted(self, resource_id):
        from packages.adapters.storage import idempotency

        marker = hashlib.sha256(("office-resource-deleted:" + resource_id).encode()).hexdigest()
        with self.p.engine.connect() as c:
            return (
                c.execute(select(idempotency.c.key).where(idempotency.c.key == marker)).first()
                is not None
            )

    def row(self, resource_id, user, conn, edit=False, allow_archived=False):
        row = (
            conn.execute(select(resources).where(resources.c.id == resource_id)).mappings().first()
        )
        if not row or (
            row["owner_id"] != user
            and (row["visibility"] != "public" or not row["published_version"] or row["archived"])
        ):
            raise DomainError("NOT_FOUND", status=404)
        if edit and (row["owner_id"] != user or row["builtin"]):
            raise DomainError("RESOURCE_READ_ONLY", "仅创建者可以维护自己的资源", 403)
        if row["archived"] and not allow_archived:
            raise DomainError("RESOURCE_ARCHIVED", "资源已归档", 409)
        return dict(row)

    def version(self, resource_id, version, user, conn):
        row = self.row(resource_id, user, conn)
        content = conn.execute(
            select(versions.c.content).where(
                versions.c.resource_id == resource_id, versions.c.version == version
            )
        ).scalar_one_or_none()
        if content is None:
            raise DomainError("VERSION_NOT_FOUND", "发布版本不存在", 404)
        return row, content

    def info(self, row, user, conn):
        own = row["owner_id"] == user
        content = row["draft"]
        if not own:
            content = conn.execute(
                select(versions.c.content).where(
                    versions.c.resource_id == row["id"],
                    versions.c.version == row["published_version"],
                )
            ).scalar_one()
        return ResourceInfo(
            id=row["id"],
            kind=row["kind"],
            owner_id=row["owner_id"],
            visibility=row["visibility"],
            builtin=row["builtin"],
            archived=row["archived"],
            published_version=row["published_version"],
            content=content,
            editable=own and not row["builtin"],
            created_at=row["created_at"],
        )

    def list(self, kind, user):
        with self.p.engine.connect() as c:
            rows = (
                c.execute(
                    select(resources)
                    .where(
                        resources.c.kind == kind,
                        or_(
                            resources.c.owner_id == user,
                            (resources.c.visibility == "public")
                            & (resources.c.published_version > 0)
                            & ~resources.c.archived,
                        ),
                    )
                    .order_by(resources.c.created_at.desc())
                )
                .mappings()
                .all()
            )
            return [self.info(r, user, c) for r in rows]

    def get(self, resource_id, user):
        with self.p.engine.connect() as c:
            return self.info(self.row(resource_id, user, c, allow_archived=True), user, c)

    def normalize(self, kind, content, user, conn):
        value = (
            (SkillContent if kind == "skill" else AgentContent)
            .model_validate(content)
            .model_dump(mode="json")
        )
        if kind == "agent":
            required = set(value["tools"])
            for ref in value["skills"]:
                row, skill = self.version(ref["id"], ref["version"], user, conn)
                if row["kind"] != "skill":
                    raise DomainError("INVALID_SKILL", status=422)
                required.update(skill["tools"])
            value["tools"] = sorted(required)
        from packages.adapters.office_tools import TOOL_REGISTRY

        if set(value["tools"]) - TOOL_REGISTRY.keys():
            raise DomainError("UNKNOWN_TOOL", "只能选择工具中心已提供的工具", 422)
        return value

    def create(self, kind, user, content, resource_id=None, visibility="private", publish=False):
        if resource_id and self.deleted(resource_id):
            raise DomainError("RESOURCE_DELETED", "此资源已删除，请使用新的创建请求。", 409)
        with self.p.engine.begin() as c:
            value = self.normalize(kind, content, user, c)
            row = dict(
                id=resource_id or uid(),
                kind=kind,
                owner_id=user,
                visibility=visibility,
                builtin=False,
                archived=False,
                published_version=0,
                draft=value,
                created_at=now(),
            )
            if publish:
                self.dependencies(row, value, user, c, visibility == "public")
                row["published_version"] = 1
            c.execute(resources.insert().values(**row))
            if publish:
                c.execute(
                    versions.insert().values(
                        resource_id=row["id"], version=1, content=value, created_at=now()
                    )
                )
            return self.info(row, user, c)

    def update(self, resource_id, user, content, visibility=None, publish=False, key=None):
        import json

        from packages.adapters.storage import idempotency

        with self.p.engine.begin() as c:
            row = self.row(resource_id, user, c, edit=True)
            marker = hashlib.sha256(f"{user}:office-save:{resource_id}:{key}".encode()).hexdigest()
            request_hash = hashlib.sha256(
                json.dumps([content, visibility, publish], sort_keys=True).encode()
            ).hexdigest()
            if key:
                previous = c.execute(
                    select(idempotency.c.hash).where(idempotency.c.key == marker)
                ).scalar_one_or_none()
                if previous:
                    if previous != request_hash:
                        raise DomainError("IDEMPOTENCY_CONFLICT", status=409)
                    return self.info(row, user, c)
            value = self.normalize(row["kind"], content, user, c)
            changes = {"draft": value}
            target_visibility = visibility or row["visibility"]
            if publish:
                self.dependencies(row, value, user, c, target_visibility == "public")
                version = row["published_version"] + 1
                c.execute(
                    versions.insert().values(
                        resource_id=resource_id, version=version, content=value, created_at=now()
                    )
                )
                changes["published_version"] = version
            if visibility is not None:
                # Until publication, public drafts remain visible only to their owner.
                if not publish and visibility == "public" and row["published_version"]:
                    published = c.execute(
                        select(versions.c.content).where(
                            versions.c.resource_id == resource_id,
                            versions.c.version == row["published_version"],
                        )
                    ).scalar_one()
                    self.dependencies(row, published, user, c, True)
                changes["visibility"] = visibility
            c.execute(resources.update().where(resources.c.id == resource_id).values(**changes))
            if key:
                c.execute(
                    idempotency.insert().values(
                        key=marker, hash=request_hash, data={"resource_id": resource_id}
                    )
                )
            return self.info({**row, **changes}, user, c)

    def dependencies(self, row, content, user, conn, publishing_public=False):
        require_enabled = row["id"] not in GENERAL_RESOURCES
        disabled = set(
            conn.execute(select(tool_settings.c.id).where(~tool_settings.c.enabled)).scalars()
        )
        if require_enabled and set(content["tools"]) & disabled:
            raise DomainError("TOOL_DISABLED", "依赖的工具已停用，请调整配置", 409)
        if row["kind"] == "agent":
            for ref in content["skills"]:
                try:
                    skill_row, skill = self.version(ref["id"], ref["version"], user, conn)
                except DomainError as exc:
                    raise DomainError(
                        "SKILL_UNAVAILABLE",
                        "绑定的 Skill 已归档、改为私有或版本不可用，请创建者更新智能体配置",
                        409,
                    ) from exc
                if publishing_public and skill_row["visibility"] != "public":
                    raise DomainError("PRIVATE_DEPENDENCY", "请先将引用的 Skill 发布为公共", 409)
                if not set(skill["tools"]).issubset(content["tools"]):
                    raise DomainError("MISSING_TOOL", "缺少 Skill 必需工具", 409)
                if require_enabled and set(skill["tools"]) & disabled:
                    raise DomainError("TOOL_DISABLED", "Skill 依赖的工具已停用", 409)

    def publish(self, resource_id, user):
        with self.p.engine.begin() as c:
            row = self.row(resource_id, user, c, edit=True)
            value = self.normalize(row["kind"], row["draft"], user, c)
            self.dependencies(row, value, user, c, row["visibility"] == "public")
            version = row["published_version"] + 1
            c.execute(
                versions.insert().values(
                    resource_id=resource_id, version=version, content=value, created_at=now()
                )
            )
            c.execute(
                resources.update()
                .where(resources.c.id == resource_id)
                .values(published_version=version, draft=value)
            )
            return self.info({**row, "published_version": version, "draft": value}, user, c)

    def state(self, resource_id, user, visibility=None, archived=None):
        with self.p.engine.begin() as c:
            row = self.row(resource_id, user, c, edit=True, allow_archived=True)
            values = {
                k: v
                for k, v in dict(visibility=visibility, archived=archived).items()
                if v is not None
            }
            if visibility == "public":
                if row["published_version"]:
                    content = c.execute(
                        select(versions.c.content).where(
                            versions.c.resource_id == resource_id,
                            versions.c.version == row["published_version"],
                        )
                    ).scalar_one()
                    self.dependencies(row, content, user, c, True)
            c.execute(resources.update().where(resources.c.id == resource_id).values(**values))
            return self.info({**row, **values}, user, c)

    def delete(self, resource_id, user):
        """Remove the resource and releases; task messages and files remain independent."""
        from packages.adapters.storage import idempotency

        with self.p.engine.begin() as c:
            row = self.row(resource_id, user, c, edit=True, allow_archived=True)
            if row["kind"] == "skill":
                # Check every release as well as drafts, including archived agents which
                # could be restored. Never silently remove somebody else's dependency.
                contents = list(
                    c.execute(
                        select(resources.c.draft).where(resources.c.kind == "agent")
                    ).scalars()
                )
                contents += list(
                    c.execute(
                        select(versions.c.content)
                        .join(resources, resources.c.id == versions.c.resource_id)
                        .where(resources.c.kind == "agent")
                    ).scalars()
                )
                if any(
                    ref["id"] == resource_id
                    for content in contents
                    for ref in content.get("skills", [])
                ):
                    raise DomainError(
                        "RESOURCE_IN_USE",
                        "此 Skill 仍被智能体草稿或发布版本引用，请先删除相关智能体；需要停用并保留引用时可使用归档。",
                        409,
                    )
            for execution in c.execute(
                select(executions.c.data).where(executions.c.status.in_(["running", "queued"]))
            ).scalars():
                if execution.get("payload", {}).get("agent_id") == resource_id:
                    raise DomainError(
                        "RESOURCE_BUSY",
                        "此智能体有任务正在执行或排队，请先取消或等待任务结束。",
                        409,
                    )
            c.execute(versions.delete().where(versions.c.resource_id == resource_id))
            c.execute(resources.delete().where(resources.c.id == resource_id))
            # A content-free marker prevents legacy import or an old create retry
            # from resurrecting a resource after its content has been removed.
            marker = hashlib.sha256(("office-resource-deleted:" + resource_id).encode()).hexdigest()
            c.execute(
                idempotency.insert().values(key=marker, hash=marker, data={"deleted": resource_id})
            )

    def snapshot(self, resource_id, version, user):
        with self.p.engine.connect() as c:
            row = self.row(resource_id, user, c)
            version = version or row["published_version"]
            row, content = self.version(resource_id, version, user, c)
            self.dependencies(row, content, user, c)
            if resource_id in GENERAL_RESOURCES:
                disabled = set(
                    c.execute(select(tool_settings.c.id).where(~tool_settings.c.enabled)).scalars()
                )
                content = {**content, "tools": [t for t in content["tools"] if t not in disabled]}
            skills = []
            for ref in content.get("skills", []):
                _, skill = self.version(ref["id"], ref["version"], user, c)
                skills.append(
                    {
                        "id": ref["id"],
                        "version": ref["version"],
                        "title": skill["title"],
                        "markdown": skill_markdown(skill),
                    }
                )
            return {"id": resource_id, "version": version, "content": content, "skills": skills}

    def seed(self, admin):
        from packages.adapters.office_tools import TOOL_REGISTRY

        samples = [
            (
                "general-assistant",
                "通用问答与写作",
                "日常问答、文字润色和需求整理，也可上传材料进行办公处理。",
                list(TOOL_REGISTRY),
                "先理解用户当前的问题，日常问答、写作和润色可以直接用对话回答，"
                "不要求用户先配置技能或上传材料，也不强制生成文件。需求不明确时先询问。"
                "用户提供附件时，先调用相应读取工具，再根据真实内容整理、计算或改写；"
                "数值和日期计算使用工具。用户要求生成文件时，必须实际调用生成工具，"
                "只说明工具返回的真实产物，不编造材料、执行结果或下载链接。"
                "可说明平台内的周报、材料检查和表格汇总助手适合什么任务。"
                "本助手只处理问答和办公任务，不发起监测、PID 调优或设备操作。",
            ),
            (
                "weekly-report",
                "工作记录转周报",
                "读取工作记录，汇总完成情况、异常和待办，生成周报文件。",
                ["read_table", "transform_table", "validate_table", "generate_document"],
                "先读取并检查工作记录的字段，缺少必要信息时向用户询问。使用工具汇总完成情况、延期事项和待办；根据真实记录编写周报，最后调用文档生成工具输出 DOCX。不得编造记录或声称文件已经生成。",
            ),
            (
                "material-review",
                "材料完整性检查",
                "检查文档是否覆盖用户要求的事项，生成检查清单。",
                ["read_document", "export_text"],
                "分段读取材料，先明确检查标准；区分缺失、矛盾和待确认内容，引用材料中的位置。生成 Markdown 检查清单，不能把推测当作原文事实。",
            ),
            (
                "table-summary",
                "表格汇总",
                "检查并整理表格，按指定字段汇总并导出。",
                ["read_table", "transform_table", "validate_table", "generate_spreadsheet"],
                "读取工作表和字段，确认分组列与统计列；检查缺失、类型和重复，使用表格工具完成统计，不用语言模型口算。输出处理说明和汇总表格。",
            ),
        ]
        for key, title, description, tools, instructions in samples:
            skill_id, agent_id = "builtin-skill-" + key, "builtin-agent-" + key
            if not self.exists(skill_id):
                self.create(
                    "skill",
                    admin,
                    dict(
                        title=title, description=description, instructions=instructions, tools=tools
                    ),
                    resource_id=skill_id,
                )
                self.publish(skill_id, admin)
                self.state(skill_id, admin, visibility="public")
            if not self.exists(agent_id):
                self.create(
                    "agent",
                    admin,
                    dict(
                        title="通用助手" if key == "general-assistant" else title + "助手",
                        description=description,
                        greeting=(
                            "你好，可以直接提问、让我帮你写作或整理思路，也可以上传材料一起处理。"
                            if key == "general-assistant"
                            else "请上传需要处理的材料，并说明你的要求。"
                        ),
                        examples=(
                            [
                                "帮我把一段工作说明写得更清楚。",
                                "帮我整理一个会议议程。",
                                "我有一份表格，应该怎样汇总？",
                            ]
                            if key == "general-assistant"
                            else [description]
                        ),
                        skills=[dict(id=skill_id, version=1)],
                        tools=tools,
                    ),
                    resource_id=agent_id,
                )
                self.publish(agent_id, admin)
                self.state(agent_id, admin, visibility="public")
            with self.p.engine.begin() as c:
                c.execute(
                    resources.update()
                    .where(resources.c.id.in_([skill_id, agent_id]))
                    .values(builtin=True)
                )
        self.upgrade_general_tools()

    def upgrade_general_tools(self):
        """Release new capabilities without changing a version pinned by an existing task."""
        from packages.adapters.office_tools import TOOL_REGISTRY

        with self.p.engine.begin() as c:
            skill_id = "builtin-skill-general-assistant"
            for resource_id in (skill_id, "builtin-agent-general-assistant"):
                row = (
                    c.execute(select(resources).where(resources.c.id == resource_id))
                    .mappings()
                    .one()
                )
                content = c.execute(
                    select(versions.c.content).where(
                        versions.c.resource_id == resource_id,
                        versions.c.version == row["published_version"],
                    )
                ).scalar_one()
                updated = {**content, "tools": sorted(set(content["tools"]) | TOOL_REGISTRY.keys())}
                if resource_id != skill_id:
                    skill_version = c.execute(
                        select(resources.c.published_version).where(resources.c.id == skill_id)
                    ).scalar_one()
                    updated["skills"] = [
                        {**ref, "version": skill_version} if ref["id"] == skill_id else ref
                        for ref in content["skills"]
                    ]
                if set(updated["tools"]) == set(content["tools"]) and updated.get(
                    "skills"
                ) == content.get("skills"):
                    continue
                version = row["published_version"] + 1
                c.execute(
                    versions.insert().values(
                        resource_id=resource_id, version=version, content=updated, created_at=now()
                    )
                )
                c.execute(
                    resources.update()
                    .where(resources.c.id == resource_id)
                    .values(draft=updated, published_version=version)
                )
