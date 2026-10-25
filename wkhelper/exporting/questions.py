"""题目导出功能。"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError, OutputExistsError
from wkhelper.domain.exporting import ExportResult
from wkhelper.domain.homework import Homework
from wkhelper.exporting.markdown import html_to_md
from wkhelper.exporting.resources import (
    _extract_extension,
    _safe_filename_part,
    download_resource_file,
    localize_text_resources,
)
from wkhelper.homework.service import get_homeworks
from wkhelper.infra.files import publish_file_no_clobber
from wkhelper.platform.base import BasePlatform

MAX_WORKERS_DOWNLOAD = 10

logger = logging.getLogger(__name__)

__all__ = [
    "export_questions_to_markdown",
]


async def export_questions_to_markdown(
    platform: BasePlatform,
    course: Course,
    output_path: str | Path | None = None,
    download_resources: bool = False,
) -> ExportResult:
    """将课程题目导出为 Markdown 文件，使用临时文件原子保存，可选下载配图与附件。"""
    homeworks = await get_homeworks(platform, course)
    if not homeworks:
        return ExportResult(file_path=None, homeworks_count=0, questions_count=0)

    if output_path is None:
        base_dir = Path(__file__).parent.parent.parent
        export_dir = base_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        safe_course_name = "".join(c if c.isalnum() or c in " _-[]()" else "_" for c in course.name)
        target_path = export_dir / f"{safe_course_name}_题目.md"
    else:
        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        raise OutputExistsError(f"输出文件已存在，拒绝覆盖: {target_path}")

    semaphore = asyncio.Semaphore(MAX_WORKERS_DOWNLOAD)
    hw_questions: dict[str | int, list[dict[str, Any]]] = {}
    failures: list[str] = []

    async def _fetch(hw: Homework) -> None:
        try:
            async with semaphore:
                questions = await platform.get_leaf_questions(hw.id, course)
            hw_questions[hw.id] = questions
        except Exception as e:
            failures.append(f"{hw.name} (ID:{hw.id}): {e}")
            logger.warning(f"获取作业 {hw.name} 题目失败: {e}")

    async with asyncio.TaskGroup() as tg:
        for hw in homeworks:
            tg.create_task(_fetch(hw))

    if failures:
        detail = "；".join(failures)
        raise APIError(f"抓取作业题目失败（{len(failures)}/{len(homeworks)} 份失败，已中止导出）: {detail}")

    if not hw_questions or not any(hw_questions.values()):
        raise APIError(f"未能从任何作业中抓取题目（共 {len(homeworks)} 份，已中止导出）: 所有作业均未返回题目")

    total_questions = 0
    downloaded_resources_total = 0
    assets_dir = target_path.parent / "assets" if download_resources else None
    if assets_dir:
        assets_dir.mkdir(parents=True, exist_ok=True)

    temp_fd, temp_file_path = tempfile.mkstemp(prefix="wk_export_", suffix=".md", dir=str(target_path.parent))
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(f"# {course.name} 题目汇总\n\n")

            for hw in homeworks:
                if hw.id not in hw_questions or not hw_questions[hw.id]:
                    continue
                questions = hw_questions[hw.id]
                f.write(f"## {hw.name}\n\n")

                for idx, q in enumerate(questions, 1):
                    total_questions += 1
                    content = q.get("content") or {}
                    body = content.get("Body") or content.get("body") or "未知题干"
                    body = html_to_md(body)

                    qid_str = str(q.get("problem_id") or q.get("id") or idx)
                    prefix = f"hw_{hw.id}_q_{qid_str}"

                    if assets_dir:
                        body, body_res = await localize_text_resources(
                            platform.fetch_bytes, body, assets_dir, f"{prefix}_body", relative_base="./assets"
                        )
                        downloaded_resources_total += len(body_res)

                    f.write(f"### 第 {idx} 题\n\n")
                    f.write(f"**题目 ID：** `{qid_str}`\n\n")
                    f.write(f"**题目：**\n{body}\n\n")

                    options = content.get("Options") or []
                    if options:
                        f.write("**选项：**\n")
                        for opt in options:
                            key = opt.get("key", "")
                            value = html_to_md(opt.get("value", ""))
                            if assets_dir:
                                key_safe = _safe_filename_part(str(key))
                                value, opt_res = await localize_text_resources(
                                    platform.fetch_bytes, value, assets_dir, f"{prefix}_opt_{key_safe}", relative_base="./assets"
                                )
                                downloaded_resources_total += len(opt_res)
                            value = value.replace("\n", "\n    ")
                            f.write(f"- **{key}**: {value}\n")
                        f.write("\n")

                    raw_attachments = (
                        content.get("Accessories")
                        or content.get("accessory_list")
                        or content.get("attachments")
                        or content.get("download")
                        or []
                    )
                    if raw_attachments and isinstance(raw_attachments, list) and assets_dir:
                        attach_lines = []
                        for a_idx, att in enumerate(raw_attachments, 1):
                            if isinstance(att, dict):
                                att_url = att.get("file_url") or att.get("url") or att.get("download_url")
                                att_name = att.get("file_name") or att.get("name") or f"附件{a_idx}"
                                if att_url:
                                    ext = _extract_extension(att_url, default=".bin")
                                    safe_name = _safe_filename_part(att_name)
                                    fn = f"{prefix}_attach_{a_idx}_{safe_name}{ext}"
                                    dest_p = assets_dir / fn
                                    ok = await download_resource_file(platform.fetch_bytes, att_url, dest_p)
                                    if ok:
                                        downloaded_resources_total += 1
                                        attach_lines.append(f"- 附件：[{att_name}](./assets/{fn})")
                                    else:
                                        attach_lines.append(f"- 附件：[{att_name}]({att_url})")
                        if attach_lines:
                            f.write("**题目附件：**\n" + "\n".join(attach_lines) + "\n\n")

                    f.write("---\n\n")
        publish_file_no_clobber(temp_file_path, target_path)
    finally:
        if os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except OSError:
                pass

    return ExportResult(
        file_path=str(target_path),
        homeworks_count=len(hw_questions),
        questions_count=total_questions,
        resources_count=downloaded_resources_total,
    )
