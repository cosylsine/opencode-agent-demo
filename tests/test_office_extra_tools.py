"""Actual office artifacts and batch file boundaries, without model inference."""

import io
import zipfile

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select

from packages.adapters.office_resources import ResourceService
from packages.adapters.office_tools import TOOL_REGISTRY, validate_arguments
from packages.adapters.platform_store import resources, versions
from packages.domain.core import DomainError
from tests.test_office_platform import headers, register, upload, wait_execution
from tests.test_office_platform import office as office_fixture

office = office_fixture


def run(client, tool, arguments, succeeds=True):
    response = client.post(
        "/api/v1/tool-executions",
        json={"tool": tool, "arguments": arguments},
        headers=headers(client),
    )
    assert response.status_code == 202, response.text
    result = wait_execution(client, response.json()["id"])
    assert result["status"] == ("succeeded" if succeeds else "failed"), result
    return result


def content(client, execution):
    response = client.get(execution["files"][0]["download_url"])
    assert response.status_code == 200
    return response.content


def pdf(widths):
    writer, raw = PdfWriter(), io.BytesIO()
    for width in widths:
        writer.add_blank_page(width, 200)
    writer.write(raw)
    return raw.getvalue()


def test_pdf_page_selection_merge_rotation_and_errors(office):
    _, client = office
    first = upload(client, "甲.pdf", pdf([100, 120, 140]))
    second = upload(client, "乙.pdf", pdf([160]))
    merged = run(client, "process_pdf", {"file_ids": [second["id"], first["id"]]})
    assert [p.mediabox.width for p in PdfReader(io.BytesIO(content(client, merged))).pages] == [
        160,
        100,
        120,
        140,
    ]
    extracted = run(
        client, "process_pdf", {"file_ids": [first["id"]], "operation": "extract", "pages": "3,1-2"}
    )
    assert [p.mediabox.width for p in PdfReader(io.BytesIO(content(client, extracted))).pages] == [
        140,
        100,
        120,
    ]
    rotated = run(
        client,
        "process_pdf",
        {"file_ids": [first["id"]], "operation": "rotate", "pages": "2", "rotation": 90},
    )
    assert [p.rotation for p in PdfReader(io.BytesIO(content(client, rotated))).pages] == [0, 90, 0]
    for pages in ["0", "4", "2-1", "1,1", "1,2-3,3"]:
        failed = run(
            client,
            "process_pdf",
            {"file_ids": [first["id"]], "operation": "extract", "pages": pages},
            False,
        )
        assert not failed["files"]


def test_table_merge_alignment_clean_conversion_and_formula_literals(office):
    _, client = office
    first = upload(client, "甲.csv", "姓名,工时\n 张三 ,2\n＝1+1,3\n".encode())
    second = upload(client, "乙.csv", "工时,姓名\n4,李四\n".encode())
    third = upload(client, "丙.csv", "姓名,部门\n王五,一部\n".encode())
    merged = run(
        client, "merge_tables", {"file_ids": [first["id"], second["id"]], "filename": "汇总.csv"}
    )
    text = content(client, merged).decode("utf-8-sig")
    assert "李四,4,乙.csv" in text and merged["result"]["total_rows"] == 3
    run(client, "merge_tables", {"file_ids": [first["id"], third["id"]]}, False)
    union = run(
        client,
        "merge_tables",
        {"file_ids": [first["id"], third["id"]], "alignment": "union", "source_column": ""},
    )
    assert union["result"]["headers"] == ["姓名", "工时", "部门"]
    assert union["result"]["total_rows"] == 3
    cleaned = run(
        client,
        "clean_table",
        {"file_id": first["id"], "normalize_width": True, "filename": "干净.csv"},
    )
    text = content(client, cleaned).decode("utf-8-sig")
    assert "张三,2" in text and "'=1+1,3" in text
    assert " 张三 " in client.get(first["download_url"]).content.decode()


def test_text_statistics_clean_deduplicate_compare_and_redact(office):
    _, client = office
    dedup = run(
        client,
        "process_text",
        {"content": " Ａ \n\nA\nＢ\nA", "operation": "deduplicate", "normalize_width": True},
    )
    assert content(client, dedup).decode() == "A\nB"
    clean = run(client, "process_text", {"content": " 甲  \n\n\n 乙 \n"})
    assert content(client, clean).decode() == "甲\n\n乙"
    stats = run(client, "process_text", {"content": "你好 world", "operation": "statistics"})
    assert "汉字：2" in stats["result"]["text"] and "英文单词：1" in stats["result"]["text"]
    old = upload(client, "旧版.txt", "标题\n旧内容\n".encode())
    diff = run(
        client, "compare_documents", {"file_id": old["id"], "other_content": "标题\n新内容\n"}
    )
    assert (
        "-旧内容" in content(client, diff).decode() and "+新内容" in content(client, diff).decode()
    )
    redacted = run(
        client,
        "redact_text",
        {
            "content": "张三 13812345678 110101199001011234 a@test.cn 6222021234567890123",
            "categories": ["phone", "id_card", "email", "bank_card"],
            "custom_terms": ["张三", "张"],
        },
    )
    text = content(client, redacted).decode()
    for secret in ["张三", "13812345678", "110101199001011234", "a@test.cn", "6222021234567890123"]:
        assert secret not in text
    assert text.count("已遮盖]") == 5
    failed = client.post(
        "/api/v1/tool-executions",
        json={"tool": "process_text", "arguments": {"content": "双重输入", "file_id": old["id"]}},
        headers=headers(client),
    )
    assert failed.status_code == 422


def test_workday_holidays_makeup_reverse_and_zero(office):
    _, client = office
    common = {"date": "2026-10-19", "holidays": ["2026-10-20"], "working_dates": ["2026-10-24"]}
    diff = run(client, "workday_calculate", {**common, "other_date": "2026-10-25"})
    assert "4 个工作日" in diff["result"]["text"]
    deadline = run(client, "workday_calculate", {**common, "operation": "add_days", "days": 4})
    assert "2026-10-24" in deadline["result"]["text"]
    reverse = run(
        client, "workday_calculate", {"date": "2026-10-19", "operation": "add_days", "days": -1}
    )
    assert "2026-10-16" in reverse["result"]["text"]
    zero = run(
        client, "workday_calculate", {"date": "2026-10-18", "operation": "add_days", "days": 0}
    )
    assert "2026-10-18" in zero["result"]["text"]
    run(
        client,
        "workday_calculate",
        {**common, "other_date": "2026-10-25", "working_dates": common["holidays"]},
        False,
    )


def test_bundle_names_contents_private_owner_and_task_scope(office):
    app, client = office
    first, second = upload(client, "材料.txt", b"first"), upload(client, "材料.txt", b"second")
    bundle = run(client, "bundle_files", {"file_ids": [first["id"], second["id"]]})
    with zipfile.ZipFile(io.BytesIO(content(client, bundle))) as archive:
        assert archive.namelist() == ["材料.txt", "材料 (2).txt"]
        assert archive.read("材料 (2).txt") == b"second"
    admin = client.get("/api/v1/auth/me").json()["user"]["id"]
    import asyncio

    with pytest.raises(DomainError) as exc:
        asyncio.run(
            app.state.office.runner.run(
                "bundle_files",
                {"file_ids": [first["id"], second["id"]]},
                admin,
                "test-scope",
                allowed={first["id"]},
            )
        )
    assert exc.value.code == "FILE_FORBIDDEN"
    register(client, "batch-member")
    failed = run(client, "bundle_files", {"file_ids": [first["id"]]}, False)
    assert not failed["files"]
    for ids in [[], [first["id"]] * 21]:
        with pytest.raises(DomainError):
            validate_arguments("bundle_files", {"file_ids": ids})


def test_general_assistant_adds_new_release_without_mutating_old_versions(office):
    app, client = office
    service = ResourceService(app.state.platform)
    admin = client.get("/api/v1/auth/me").json()["user"]["id"]
    agent, skill = "builtin-agent-general-assistant", "builtin-skill-general-assistant"
    originals = {}
    # Simulate the previously deployed pair with only the first nine capabilities.
    with app.state.db.engine.begin() as c:
        for resource_id in [agent, skill]:
            value = dict(
                c.execute(
                    select(versions.c.content).where(versions.c.resource_id == resource_id)
                ).scalar_one()
            )
            value["tools"] = [t for t in value["tools"] if t in list(TOOL_REGISTRY)[:9]]
            originals[resource_id] = value
            c.execute(
                versions.update().where(versions.c.resource_id == resource_id).values(content=value)
            )
            c.execute(resources.update().where(resources.c.id == resource_id).values(draft=value))
    service.seed(admin)
    service.seed(admin)
    for resource_id in [agent, skill]:
        assert service.get(resource_id, admin).published_version == 2
        with app.state.db.engine.connect() as c:
            assert (
                c.execute(
                    select(versions.c.content).where(
                        versions.c.resource_id == resource_id, versions.c.version == 1
                    )
                ).scalar_one()
                == originals[resource_id]
            )
    old = service.snapshot(agent, 1, admin)
    new = service.snapshot(agent, None, admin)
    assert len(old["content"]["tools"]) == 9 and len(new["content"]["tools"]) == 17
    assert old["skills"][0]["version"] == 1 and new["skills"][0]["version"] == 2
