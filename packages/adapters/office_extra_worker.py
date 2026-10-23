"""Offline batch office operations, executed inside the bounded office worker."""

import datetime as dt
import difflib
import re
import unicodedata
import zipfile
from pathlib import Path

EXTRA_TOOLS = {
    "process_pdf",
    "merge_tables",
    "clean_table",
    "process_text",
    "compare_documents",
    "redact_text",
    "workday_calculate",
    "bundle_files",
}


def text_source(a, paths, prefix="", limit=100000):
    from packages.adapters.office_worker import document_text

    text = (
        document_text(paths[prefix + "file_id"])
        if a.get(prefix + "file_id")
        else a[prefix + "content"]
    )
    if len(text) > limit:
        raise ValueError(f"文本超过 {limit} 字符，请先拆分")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def text_artifact(output, filename, text, force_txt=False):
    suffix = ".md" if filename.lower().endswith(".md") and not force_txt else ".txt"
    path = output / ("result" + suffix)
    path.write_text(text or "（结果为空）\n", encoding="utf-8")
    return {"path": str(path), "name": Path(filename.replace("\\", "/")).stem[:100] + suffix}


def selected_pages(value, count):
    if not value.strip():
        return list(range(count))
    pages = []
    for token in re.split(r"[,，]", value):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", token)
        if not match:
            raise ValueError("页码格式应为 1-3,5")
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= count:
            raise ValueError(f"页码超出范围（共 {count} 页），或范围倒置")
        pages.extend(range(start - 1, end))
        if len(pages) > 1000:
            raise ValueError("最多输出 1000 页")
    if len(set(pages)) != len(pages):
        raise ValueError("页码不能重复")
    return pages


def execute_extra(tool, a, paths, output):
    from packages.adapters.office_worker import spreadsheet, table

    if tool == "process_pdf":
        from pypdf import PdfReader, PdfWriter

        if a["operation"] != "merge" and len(paths["file_ids"]) != 1:
            raise ValueError("提取或旋转页面时请选择一份 PDF")
        if a["operation"] == "merge" and a["pages"].strip():
            raise ValueError("合并时使用各文件的全部页面，请留空页码")
        writer, total = PdfWriter(), 0
        for source in paths["file_ids"]:
            if Path(source).suffix != ".pdf":
                raise ValueError("此工具只接受 PDF")
            reader = PdfReader(source)
            if reader.is_encrypted:
                raise ValueError("暂不支持加密 PDF")
            total += len(reader.pages)
            if total > 1000:
                raise ValueError("输入合计超过 1000 页，请先拆分")
            selection = selected_pages(a["pages"], len(reader.pages))
            if a["operation"] == "rotate":
                selected = set(selection)
                for i, page in enumerate(reader.pages):
                    writer.add_page(page.rotate(a["rotation"]) if i in selected else page)
            else:
                for i in selection:
                    writer.add_page(reader.pages[i])
        if not writer.pages:
            raise ValueError("PDF 没有可输出的页面")
        path = output / "result.pdf"
        writer.write(path)
        return {"text": f"已输出 {len(writer.pages)} 页 PDF。"}, [
            {"path": str(path), "name": Path(a["filename"].replace("\\", "/")).stem[:100] + ".pdf"}
        ]

    if tool in ("merge_tables", "clean_table"):
        warnings, rows, headers = [], [], []
        if tool == "merge_tables":
            for source, name in zip(paths["file_ids"], paths["file_names"]):
                hs, rs, _, ws = table(source)
                warnings.extend(ws)
                if a["source_column"] and a["source_column"] in hs:
                    raise ValueError("来源列名称与原有字段重复，请换一个名称或留空")
                if headers and a["alignment"] == "strict" and set(hs) != set(headers):
                    raise ValueError("表头字段不一致，请整理字段或选择按字段名取并集")
                headers.extend(h for h in hs if h not in headers)
                rows.extend(
                    {**r, **({a["source_column"]: name} if a["source_column"] else {})} for r in rs
                )
                if len(headers) + bool(a["source_column"]) > 200 or len(rows) > 100000:
                    raise ValueError("合并结果超过 100000 行或 200 列")
            if a["source_column"]:
                headers.append(a["source_column"])
        else:
            headers, rows, _, warnings = table(paths["file_id"], a["sheet"])
            columns = a["columns"] or headers
            if any(c not in headers for c in columns):
                raise ValueError("指定字段不存在")
            changed = 0
            for row in rows:
                for column in columns:
                    old = row[column]
                    if isinstance(old, str):
                        value = unicodedata.normalize("NFKC", old) if a["normalize_width"] else old
                        if a["trim"]:
                            value = value.strip()
                        row[column] = value
                        changed += value != old
            warnings = [*warnings, f"已修改 {changed} 个单元格；按值导出，不保留原样式与公式。"]
        artifact = spreadsheet(
            output, a["filename"], headers, [[r.get(c) for c in headers] for r in rows]
        )
        return {
            "headers": headers,
            "rows": rows[:30],
            "total_rows": len(rows),
            "warnings": list(dict.fromkeys(warnings)),
        }, [artifact]

    if tool == "process_text":
        original = text_source(a, paths)
        text = original
        if a["operation"] != "statistics":
            if a["normalize_width"]:
                text = unicodedata.normalize("NFKC", text)
            lines = [line.strip() for line in text.splitlines()]
            if a["operation"] == "deduplicate":
                lines = list(dict.fromkeys(line for line in lines if line))
            else:
                lines = [line for i, line in enumerate(lines) if line or i and lines[i - 1]]
            text = "\n".join(lines).strip()
        nonspace = len(re.sub(r"\s", "", text))
        chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
        words = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)*", text))
        stats = f"字符数：{len(text)}；不含空白：{nonspace}；汉字：{chinese}；英文单词：{words}；行数：{len(text.splitlines())}。"
        report = stats if a["operation"] == "statistics" else stats + "\n\n" + text[:16000]
        return {"text": report}, [
            text_artifact(output, a["filename"], stats if a["operation"] == "statistics" else text)
        ]

    if tool == "compare_documents":
        before, after = text_source(a, paths, limit=50000), text_source(a, paths, "other_", 50000)
        diff = list(
            difflib.unified_diff(
                before.splitlines(), after.splitlines(), fromfile="旧版", tofile="新版", lineterm=""
            )
        )
        text = "\n".join(diff) if diff else "两版文本相同。"
        return {
            "text": text[:16000],
            "warnings": ["按行比较纯文本；不比较版式、批注、修订或图片。完整结果见文件。"],
        }, [text_artifact(output, a["filename"], text, True)]

    if tool == "redact_text":
        text, counts = text_source(a, paths), []
        # Match longer identifiers before phone numbers; never report the original values.
        patterns = {
            "id_card": ("身份证号", r"(?<!\d)\d{17}[\dXx](?!\d)"),
            "bank_card": ("银行卡号", r"(?<!\d)\d{16,19}(?!\d)"),
            "phone": ("手机号", r"(?<!\d)1[3-9]\d{9}(?!\d)"),
            "email": (
                "邮箱",
                r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
            ),
        }
        terms = sorted(set(a["custom_terms"]), key=lambda s: (-len(s), s))
        if terms:
            text, count = re.subn(
                "|".join(re.escape(t) for t in terms), lambda _: "[指定词语已遮盖]", text
            )
            counts.append(f"指定词语：{count} 处")
        for category, (label, pattern) in patterns.items():
            if category in a["categories"]:
                text, count = re.subn(pattern, lambda _, label=label: f"[{label}已遮盖]", text)
                counts.append(f"{label}：{count} 处")
        return {
            "text": "；".join(counts) + "\n\n" + text[:16000],
            "warnings": [
                "仅按所选格式和指定词语匹配，可能漏报或误报；分享前请人工复核。输出为纯文本，不保留原文档格式。"
            ],
        }, [text_artifact(output, a["filename"], text)]

    if tool == "workday_calculate":
        start = dt.date.fromisoformat(a["date"])
        holidays = {dt.date.fromisoformat(d) for d in a["holidays"]}
        working = {dt.date.fromisoformat(d) for d in a["working_dates"]}
        if holidays & working:
            raise ValueError("同一天不能同时设为放假和补班")

        def is_workday(d):
            return d in working or (d.weekday() < 5 and d not in holidays)

        if a["operation"] == "difference":
            end = dt.date.fromisoformat(a["other_date"])
            delta = (end - start).days
            if abs(delta) > 36600:
                raise ValueError("日期区间不能超过 100 年")
            step = 1 if delta >= 0 else -1
            count = (
                sum(
                    is_workday(start + dt.timedelta(days=i * step))
                    for i in range(1, abs(delta) + 1)
                )
                * step
            )
            text = f"相差 {count} 个工作日（不含起始日，包含结束日）。"
        else:
            remaining, current, step = abs(a["days"]), start, 1 if a["days"] >= 0 else -1
            while remaining:
                current += dt.timedelta(days=step)
                remaining -= is_workday(current)
            text = f"计算结果：{current.isoformat()}（增加 {a['days']} 个工作日）。"
        return {
            "text": text,
            "warnings": [
                "以周一至周五为基础，仅应用你填写的放假和补班日期，未自动加载节假日日历。"
            ],
        }, []

    if tool == "bundle_files":
        path, used, names, total = output / "result.zip", set(), [], 0
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for source, original in zip(paths["file_ids"], paths["file_names"]):
                name = Path(original.replace("\\", "/")).name
                total += Path(source).stat().st_size
                if total > 100 * 1024 * 1024:
                    raise ValueError("打包原文件合计不能超过 100 MiB")
                stem, suffix, i = Path(name).stem, Path(name).suffix, 2
                while name.casefold() in used:
                    name = f"{stem} ({i}){suffix}"
                    i += 1
                used.add(name.casefold())
                archive.write(source, name)
                names.append(name)
        return {"text": f"已打包 {len(names)} 份文件：\n" + "\n".join(names)}, [
            {"path": str(path), "name": Path(a["filename"].replace("\\", "/")).stem[:100] + ".zip"}
        ]
    raise ValueError("未知工具")
