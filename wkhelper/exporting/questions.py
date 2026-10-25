"""题目导出功能。"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError, OutputExistsError
from wkhelper.domain.exporting import CourseQuestionsExport, ExportedHomeworkItem, ExportedQuestionItem, ExportResult
from wkhelper.domain.homework import Homework, OptionItem
from wkhelper.domain.questions import question_type
from wkhelper.exporting.markdown import html_to_md
from wkhelper.exporting.resources import (
    _extract_extension,
    _safe_filename_part,
    download_resource_file,
    localize_text_resources,
)
from wkhelper.homework.service import get_homeworks
from wkhelper.infra.files import publish_file_no_clobber
from wkhelper.infra.serialization import encode_json
from wkhelper.platform.base import BasePlatform

MAX_WORKERS_DOWNLOAD = 10

logger = logging.getLogger(__name__)

__all__ = [
    "export_questions",
    "export_questions_to_markdown",
]


async def export_questions(
    platform: BasePlatform,
    course: Course,
    output_path: str | Path | None = None,
    download_resources: bool = False,
    format: Literal["md", "markdown", "json"] | None = None,
) -> ExportResult:
    """导出课程题目为 Markdown 或 JSON，使用临时文件原子保存，支持后缀自动推断。"""
    homeworks = await get_homeworks(platform, course)
    if not homeworks:
        return ExportResult(file_path=None, homeworks_count=0, questions_count=0)

    resolved_format = _resolve_export_format(output_path, format)

    if output_path is None:
        base_dir = Path(__file__).parent.parent.parent
        export_dir = base_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        safe_course_name = "".join(c if c.isalnum() or c in " _-[]()" else "_" for c in course.name)
        ext = ".json" if resolved_format == "json" else ".md"
        target_path = export_dir / f"{safe_course_name}_题目{ext}"
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

    temp_suffix = ".json" if resolved_format == "json" else ".md"
    temp_fd, temp_file_path = tempfile.mkstemp(prefix="wk_export_", suffix=temp_suffix, dir=str(target_path.parent))
    try:
        if resolved_format == "json":
            total_questions, downloaded_resources_total = await _write_questions_json(
                temp_fd=temp_fd,
                platform=platform,
                course=course,
                homeworks=homeworks,
                hw_questions=hw_questions,
                assets_dir=assets_dir,
            )
        else:
            total_questions, downloaded_resources_total = await _write_questions_markdown(
                temp_fd=temp_fd,
                platform=platform,
                course=course,
                homeworks=homeworks,
                hw_questions=hw_questions,
                assets_dir=assets_dir,
            )
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


def _resolve_export_format(output_path: str | Path | None, format: str | None) -> str:
    """根据显式参数或文件后缀确定导出格式（默认为 md）。"""
    if format is not None:
        fmt = format.lower().strip()
        if fmt in ("json", "js"):
            return "json"
        if fmt in ("md", "markdown"):
            return "md"
        raise ValueError(f"不支持的导出格式: {format}，仅支持 md 或 json")
    if output_path is not None:
        suffix = Path(output_path).suffix.lower()
        if suffix == ".json":
            return "json"
    return "md"


def _extract_answers(q: dict[str, Any]) -> tuple[Any, Any, bool | None]:
    """从题目原始 payload 中提取 (正确答案, 我的作答, 是否正确)。"""
    user_info = q.get("user") or {}
    content = q.get("content") or {}

    is_right: bool | None = user_info.get("is_right")
    if is_right is None:
        is_right = user_info.get("is_correct")

    raw_correct = user_info.get("answer") or user_info.get("answers") or content.get("Answer") or content.get("answer")
    raw_my = user_info.get("my_answer") or user_info.get("my_answers")

    if raw_correct is None and raw_my is not None and is_right is True:
        raw_correct = raw_my
    elif raw_my is None and raw_correct is not None and is_right is True:
        raw_my = raw_correct

    def _format(val: Any) -> Any:
        if isinstance(val, dict):
            formatted: dict[str, Any] = {}
            for k, v in val.items():
                if isinstance(v, dict) and "answer" in v:
                    formatted[str(k)] = str(v["answer"])
                elif isinstance(v, list):
                    formatted[str(k)] = [str(x) for x in v]
                else:
                    formatted[str(k)] = str(v)
            return formatted
        if isinstance(val, list):
            return [str(x) for x in val]
        if val is not None:
            return str(val)
        return None

    return _format(raw_correct), _format(raw_my), is_right


def _format_answer_display(val: Any) -> str:
    """将答案数据格式化为适合 Markdown 展示的文本。"""
    if isinstance(val, dict):
        return "；".join(f"空{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in val.items())
    if isinstance(val, list):
        return "、".join(str(x) for x in val)
    return str(val)


async def _write_questions_markdown(
    temp_fd: int,
    platform: BasePlatform,
    course: Course,
    homeworks: list[Homework],
    hw_questions: dict[str | int, list[dict[str, Any]]],
    assets_dir: Path | None,
) -> tuple[int, int]:
    """将抓取的题目数据写入 Markdown 临时文件。"""
    total_questions = 0
    downloaded_resources = 0
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
                    downloaded_resources += len(body_res)

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
                            downloaded_resources += len(opt_res)
                        value = value.replace("\n", "\n    ")
                        f.write(f"- **{key}**: {value}\n")
                    f.write("\n")

                correct_ans, my_ans, is_right = _extract_answers(q)
                if my_ans is not None:
                    status_tag = "（回答正确）" if is_right is True else ("（回答错误）" if is_right is False else "")
                    f.write(f"**我的作答：** {_format_answer_display(my_ans)} {status_tag}\n\n")
                if correct_ans is not None:
                    f.write(f"**正确答案：** {_format_answer_display(correct_ans)}\n\n")

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
                                    downloaded_resources += 1
                                    attach_lines.append(f"- 附件：[{att_name}](./assets/{fn})")
                                else:
                                    attach_lines.append(f"- 附件：[{att_name}]({att_url})")
                    if attach_lines:
                        f.write("**题目附件：**\n" + "\n".join(attach_lines) + "\n\n")
                f.write("---\n\n")
    return total_questions, downloaded_resources


async def _write_questions_json(
    temp_fd: int,
    platform: BasePlatform,
    course: Course,
    homeworks: list[Homework],
    hw_questions: dict[str | int, list[dict[str, Any]]],
    assets_dir: Path | None,
) -> tuple[int, int]:
    """将抓取的题目数据结构化序列化为 JSON 临时文件。"""
    total_questions = 0
    downloaded_resources = 0
    exported_hws: list[ExportedHomeworkItem] = []

    for hw in homeworks:
        if hw.id not in hw_questions or not hw_questions[hw.id]:
            continue
        questions = hw_questions[hw.id]
        hw_items: list[ExportedQuestionItem] = []
        for idx, q in enumerate(questions, 1):
            total_questions += 1
            content = q.get("content") or {}
            body = content.get("Body") or content.get("body") or "未知题干"
            body = html_to_md(body)
            qid_str = str(q.get("problem_id") or q.get("id") or idx)
            prefix = f"hw_{hw.id}_q_{qid_str}"
            item_res: list[str] = []

            if assets_dir:
                body, body_res = await localize_text_resources(
                    platform.fetch_bytes, body, assets_dir, f"{prefix}_body", relative_base="./assets"
                )
                downloaded_resources += len(body_res)
                item_res.extend(body_res)

            opts: list[OptionItem] = []
            raw_options = content.get("Options") or []
            for opt in raw_options:
                key = opt.get("key", "")
                value = html_to_md(opt.get("value", ""))
                if assets_dir:
                    key_safe = _safe_filename_part(str(key))
                    value, opt_res = await localize_text_resources(
                        platform.fetch_bytes, value, assets_dir, f"{prefix}_opt_{key_safe}", relative_base="./assets"
                    )
                    downloaded_resources += len(opt_res)
                    item_res.extend(opt_res)
                opts.append(OptionItem(key=str(key), text=value))

            type_code = question_type(q)
            type_label = str(type_code) if type_code is not None else None
            user_info = q.get("user") or {}
            correct_ans, my_ans, is_right = _extract_answers(q)
            hw_items.append(
                ExportedQuestionItem(
                    index=idx,
                    id=qid_str,
                    type=type_label,
                    type_id=type_code,
                    prompt=body,
                    options=opts,
                    correct_answer=correct_ans,
                    user_answer=my_ans,
                    is_correct=is_right,
                    score=user_info.get("score") or user_info.get("my_score"),
                    library_id=content.get("LibraryID") or content.get("library_id"),
                    resources=item_res,
                )
            )
        exported_hws.append(
            ExportedHomeworkItem(
                id=str(hw.id),
                name=hw.name,
                deadline=hw.deadline,
                completed=hw.completed,
                total_questions=len(hw_items),
                questions=hw_items,
            )
        )

    export_data = CourseQuestionsExport(
        course_id=str(course.id),
        course_name=course.name,
        platform_id=course.platform_id,
        total_homeworks=len(exported_hws),
        total_questions=total_questions,
        homeworks=exported_hws,
    )
    with os.fdopen(temp_fd, "wb") as f:
        f.write(encode_json(export_data))

    return total_questions, downloaded_resources


export_questions_to_markdown = export_questions
