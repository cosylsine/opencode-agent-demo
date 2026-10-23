"""Cross-account publication, revocation and deletion with preserved task history."""

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from packages.adapters.platform_store import resources, versions
from packages.domain.core import DomainError
from tests.test_office_platform import headers, register, wait_execution
from tests.test_office_platform import office as office_fixture

office = office_fixture


def create(client, kind, body):
    response = client.post("/api/v1/" + kind, json=body, headers=headers(client))
    assert response.status_code == 201, response.text
    return response.json()


def pair(client, public=True):
    visibility = "public" if public else "private"
    skill = create(
        client,
        "skills",
        {
            "title": "生命周期技能",
            "description": "导出用户文本",
            "instructions": "按要求导出文本",
            "tools": ["export_text"],
            "visibility": visibility,
            "publish": True,
        },
    )
    agent = create(
        client,
        "agents",
        {
            "title": "生命周期助手",
            "description": "处理办公文本",
            "skills": [{"id": skill["id"], "version": 1}],
            "visibility": visibility,
            "publish": True,
        },
    )
    return skill, agent


def conversation(client, agent):
    response = client.post(
        "/api/v1/conversations", json={"agent_id": agent["id"]}, headers=headers(client)
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_visibility_public_drafts_atomic_publish_and_permission_edits(office):
    app, owner = office
    member = TestClient(app)
    register(member, "lifecycle-member")
    body = {
        "title": "创建时选公共",
        "description": "说明",
        "instructions": "按要求处理",
        "visibility": "public",
    }
    draft = create(owner, "skills", body)
    sid = draft["id"]
    assert draft["visibility"] == "public" and draft["published_version"] == 0
    assert member.get("/api/v1/skills/" + sid).status_code == 404
    assert sid not in {r["id"] for r in member.get("/api/v1/skills").json()["items"]}
    saved = owner.put(
        "/api/v1/skills/" + sid,
        json={**body, "publish": True},
        headers=headers(owner, "publish-visible-draft"),
    )
    assert saved.status_code == 200 and saved.json()["published_version"] == 1
    assert member.get("/api/v1/skills/" + sid).status_code == 200
    replay = owner.put(
        "/api/v1/skills/" + sid,
        json={**body, "publish": True},
        headers=headers(owner, "publish-visible-draft"),
    )
    assert replay.json()["published_version"] == 1
    conflict = owner.put(
        "/api/v1/skills/" + sid,
        json={**body, "title": "改了内容", "publish": True},
        headers=headers(owner, "publish-visible-draft"),
    )
    assert conflict.status_code == 409
    changed = owner.put(
        "/api/v1/skills/" + sid, json={**body, "visibility": "private"}, headers=headers(owner)
    )
    assert changed.status_code == 200 and changed.json()["published_version"] == 1
    assert member.get("/api/v1/skills/" + sid).status_code == 404
    assert (
        owner.patch(
            "/api/v1/skills/" + sid, json={"visibility": "public"}, headers=headers(owner)
        ).status_code
        == 200
    )
    for method, data in [(member.put, body), (member.patch, {"visibility": "private"})]:
        assert (
            method("/api/v1/skills/" + sid, json=data, headers=headers(member)).status_code == 403
        )

    private = create(
        owner, "skills", {**body, "title": "私有依赖", "visibility": "private", "publish": True}
    )
    agent_body = {
        "title": "有私有依赖的助手",
        "description": "说明",
        "skills": [{"id": private["id"], "version": 1}],
        "visibility": "public",
        "publish": True,
    }
    count = len(owner.get("/api/v1/agents").json()["items"])
    failed = owner.post("/api/v1/agents", json=agent_body, headers=headers(owner))
    assert failed.status_code == 409 and failed.json()["error"]["code"] == "PRIVATE_DEPENDENCY"
    assert len(owner.get("/api/v1/agents").json()["items"]) == count
    agent = create(owner, "agents", {**agent_body, "visibility": "private"})
    # Replacing the old private dependency and publishing publicly must be atomic.
    updated = owner.put(
        "/api/v1/agents/" + agent["id"],
        json={**agent_body, "skills": [{"id": sid, "version": 1}]},
        headers=headers(owner, "agent-public-release"),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["published_version"] == 2 and updated.json()["visibility"] == "public"
    assert member.get("/api/v1/agents/" + agent["id"]).status_code == 200
    with app.state.db.engine.connect() as c:
        content = c.execute(
            select(versions.c.content).where(
                versions.c.resource_id == agent["id"], versions.c.version == 1
            )
        ).scalar_one()
        assert content["skills"][0]["id"] == private["id"] and "visibility" not in content


@pytest.mark.parametrize(
    "target,change",
    [
        ("skill", {"archived": True}),
        ("agent", {"archived": True}),
        ("skill", {"visibility": "private"}),
        ("agent", {"visibility": "private"}),
    ],
)
def test_archiving_or_revoking_sharing_blocks_other_users_and_inflight_calls(
    office, target, change
):
    app, owner = office
    skill, agent = pair(owner)
    member = TestClient(app)
    register(member, "revoked-member")
    task = conversation(member, agent)
    started, release = threading.Event(), threading.Event()
    blocked = []

    async def pending(snapshot, history, files, token, on_session):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        try:
            await app.state.office.call(
                token, "export_text", {"content": "不能导出", "filename": "不能生成.txt"}
            )
        except DomainError as exc:
            blocked.append(exc.code)
        return "旧权限下的答复也不能提交。"

    app.state.client.office_chat = pending
    response = member.post(
        f"/api/v1/conversations/{task['id']}/messages",
        json={"content": "开始任务"},
        headers=headers(member),
    )
    assert response.status_code == 202 and started.wait(3)
    item = skill if target == "skill" else agent
    path = "/api/v1/" + ("skills/" if target == "skill" else "agents/") + item["id"]
    try:
        assert owner.patch(path, json=change, headers=headers(owner)).status_code == 200
        assert member.get(path).status_code == 404
        listing = member.get("/api/v1/" + ("skills" if target == "skill" else "agents")).json()[
            "items"
        ]
        assert item["id"] not in {r["id"] for r in listing}
        assert member.post(
            "/api/v1/conversations", json={"agent_id": agent["id"]}, headers=headers(member)
        ).status_code in (404, 409)
        assert member.post(
            f"/api/v1/conversations/{task['id']}/messages",
            json={"content": "不能继续"},
            headers=headers(member),
        ).status_code in (404, 409)
        if target == "skill":
            assert (
                member.post(
                    "/api/v1/agents",
                    json={
                        "title": "非法引用",
                        "description": "说明",
                        "skills": [{"id": skill["id"], "version": 1}],
                    },
                    headers=headers(member),
                ).status_code
                == 404
            )
    finally:
        release.set()
    finished = wait_execution(member, response.json()["id"])
    assert finished["status"] == "failed" and not finished["files"] and blocked
    assert len(member.get(f"/api/v1/conversations/{task['id']}/messages").json()["items"]) == 1
    restored = {"archived": False} if "archived" in change else {"visibility": "public"}
    assert owner.patch(path, json=restored, headers=headers(owner)).status_code == 200
    assert member.get(path).status_code == 200
    conversation(member, agent)


def test_delete_protects_dependencies_active_runs_and_history(office):
    app, owner = office
    skill, agent = pair(owner)
    member = TestClient(app)
    register(member, "delete-member")
    assert (
        member.delete("/api/v1/agents/" + agent["id"], headers=headers(member)).status_code == 403
    )
    assert owner.delete("/api/v1/skills/" + agent["id"], headers=headers(owner)).status_code == 404
    assert (
        owner.delete("/api/v1/skills/" + skill["id"], headers=headers(owner)).json()["error"][
            "code"
        ]
        == "RESOURCE_IN_USE"
    )
    task = conversation(owner, agent)
    started, release = threading.Event(), threading.Event()

    async def produce(snapshot, history, files, token, on_session):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        await app.state.office.call(
            token, "export_text", {"content": "必须保留的旧文件", "filename": "历史文件.txt"}
        )
        return "已生成文件。"

    app.state.client.office_chat = produce
    execution = owner.post(
        f"/api/v1/conversations/{task['id']}/messages",
        json={"content": "保留历史内容"},
        headers=headers(owner),
    ).json()
    assert started.wait(3)
    try:
        busy = owner.delete("/api/v1/agents/" + agent["id"], headers=headers(owner))
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "RESOURCE_BUSY"
    finally:
        release.set()
    result = wait_execution(owner, execution["id"])
    assert result["status"] == "succeeded"
    messages_url = f"/api/v1/conversations/{task['id']}/messages"
    old_messages = owner.get(messages_url).json()
    file_url = result["files"][0]["download_url"]
    old_file = owner.get(file_url).content
    for kind, item in [("agents", agent), ("skills", skill)]:
        response = owner.delete("/api/v1/" + kind + "/" + item["id"], headers=headers(owner))
        assert response.status_code == 200, response.text
        assert owner.get("/api/v1/" + kind + "/" + item["id"]).status_code == 404
        assert member.get("/api/v1/" + kind + "/" + item["id"]).status_code == 404
        with app.state.db.engine.connect() as c:
            assert not c.execute(select(resources.c.id).where(resources.c.id == item["id"])).first()
            assert not c.execute(
                select(versions.c.resource_id).where(versions.c.resource_id == item["id"])
            ).first()
    assert owner.get(messages_url).json() == old_messages
    assert owner.get(file_url).content == old_file
    assert task["id"] in {r["id"] for r in owner.get("/api/v1/conversations").json()["items"]}
    assert (
        owner.post(messages_url, json={"content": "不能再执行"}, headers=headers(owner)).status_code
        == 404
    )
    assert member.get(messages_url).status_code == 404 and member.get(file_url).status_code == 404
    for kind, resource_id in [
        ("skills", "builtin-skill-general-assistant"),
        ("agents", "builtin-agent-general-assistant"),
    ]:
        assert (
            owner.delete("/api/v1/" + kind + "/" + resource_id, headers=headers(owner)).status_code
            == 403
        )


def test_deleted_resources_are_not_resurrected_by_retry_or_legacy_import(office, tmp_path):
    from packages.adapters.office_resources import ResourceService

    app, owner = office
    body = {"title": "删除后不能重试重建", "description": "说明", "instructions": "步骤"}
    first = owner.post("/api/v1/skills", json=body, headers=headers(owner, "original-create"))
    sid = first.json()["id"]
    assert owner.delete("/api/v1/skills/" + sid, headers=headers(owner)).status_code == 200
    replay = owner.post("/api/v1/skills", json=body, headers=headers(owner, "original-create"))
    assert replay.status_code == 409 and replay.json()["error"]["code"] == "RESOURCE_DELETED"
    admin = owner.get("/api/v1/auth/me").json()["user"]["id"]
    catalog = tmp_path / "catalog"
    skill_file = catalog / ".opencode/skills/legacy-deletion/SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: legacy-deletion\ndescription: Legacy deletion\n---\n\n# Legacy deletion\n\nWork steps.\n",
        encoding="utf-8",
    )
    app.state.platform.catalog_root = catalog
    app.state.platform.import_legacy(admin)
    legacy_id = "legacy-legacy-deletion"
    service = ResourceService(app.state.platform)
    assert service.exists(legacy_id)
    assert owner.delete("/api/v1/skills/" + legacy_id, headers=headers(owner)).status_code == 200
    app.state.platform.import_legacy(admin)
    assert not service.exists(legacy_id) and service.deleted(legacy_id)
    assert skill_file.is_file()
