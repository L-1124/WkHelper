"""题目导出功能。"""

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from wkhelper.core.base_platform import BasePlatform
from wkhelper.core.config import MAX_WORKERS_DOWNLOAD
from wkhelper.core.entities import Course, Homework
from wkhelper.core.exceptions import APIError, OutputExistsError
from wkhelper.core.markdown import WkhelperConverter, html_to_md  # noqa: F401  (对外沿用 exporter.html_to_md 入口)
from wkhelper.core.results import ExamExportResult, ExportResult, OptionItem, QuestionDetail
from wkhelper.core.transport import Fetcher

logger = logging.getLogger(__name__)


__all__ = [
    "WkhelperConverter",
    "export_exam_paper_to_markdown",
    "export_questions_to_markdown",
    "html_to_md",
    "localize_question_detail",
]


_IMG_PATTERN = re.compile(r"!\[(.*?)\]\((https?://[^\s\)\"\']+)\)")
_ATTACH_PATTERN = re.compile(
    r"\[(.*?)\]\((https?://[^\s\)\"\']+\.(?:csv|xlsx?|docx?|pdf|zip|rar|tar(?:\.gz)?|txt|json)[^\s\)\"\']*)\)",
    re.IGNORECASE,
)


def _extract_extension(url: str, default: str = ".png") -> str:
    path = unquote(urlparse(url).path)
    ext = Path(path).suffix.lower()
    return ext if ext and len(ext) <= 8 else default


def _safe_filename_part(name: str) -> str:
    cleaned = re.sub(r"[^\w\-]", "_", name).strip("_")
    return cleaned[:30] if cleaned else "res"


async def download_resource_file(
    fetch: Fetcher,
    url: str,
    dest_path: Path,
) -> bool:
    """下载单个远程资源文件到本地。"""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        return True
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        content = await fetch(url)
        if content:
            dest_path.write_bytes(content)
            return True
        logger.debug(f"下载资源失败: 空内容, url={url}")
    except Exception as exc:
        logger.debug(f"下载资源异常: url={url}, err={exc}")
    return False


async def localize_text_resources(
    fetch: Fetcher,
    text: str,
    assets_dir: Path,
    filename_prefix: str,
    relative_base: str = "./assets",
) -> tuple[str, list[str]]:
    """扫描文本中的图片与附件 URL，下载并重写为规范的本地相对路径。"""
    downloaded_paths: list[str] = []
    rewritten_text = text

    img_matches = list(_IMG_PATTERN.finditer(text))
    for seq, m in enumerate(img_matches, 1):
        _alt, url = m.group(1), m.group(2)
        ext = _extract_extension(url, default=".png")
        filename = f"{filename_prefix}_{seq}{ext}"
        dest_path = assets_dir / filename
        ok = await download_resource_file(fetch, url, dest_path)
        if ok:
            rel_path = f"{relative_base}/{filename}"
            rewritten_text = rewritten_text.replace(url, rel_path)
            downloaded_paths.append(str(dest_path))

    attach_matches = list(_ATTACH_PATTERN.finditer(rewritten_text))
    for seq, m in enumerate(attach_matches, 1):
        link_title, url = m.group(1), m.group(2)
        ext = _extract_extension(url, default=".bin")
        safe_title = _safe_filename_part(link_title)
        filename = f"{filename_prefix}_attach_{seq}_{safe_title}{ext}"
        dest_path = assets_dir / filename
        ok = await download_resource_file(fetch, url, dest_path)
        if ok:
            rel_path = f"{relative_base}/{filename}"
            rewritten_text = rewritten_text.replace(url, rel_path)
            downloaded_paths.append(str(dest_path))

    return rewritten_text, downloaded_paths


async def localize_question_detail(
    fetch: Fetcher,
    detail: QuestionDetail,
    assets_dir: Path,
    relative_base: str = "./assets",
) -> QuestionDetail:
    """下载单题题干与选项中的图片与附件，并按规范命名改写路径。"""
    prefix = f"q_{detail.id}"
    new_prompt, body_res = await localize_text_resources(fetch, detail.prompt, assets_dir, f"{prefix}_body", relative_base=relative_base)
    all_res = list(body_res)

    new_options: list[OptionItem] = []
    for opt in detail.options:
        key_str = _safe_filename_part(str(opt.key))
        new_text, opt_res = await localize_text_resources(
            fetch, opt.text, assets_dir, f"{prefix}_opt_{key_str}", relative_base=relative_base
        )
        new_options.append(OptionItem(key=opt.key, text=new_text))
        all_res.extend(opt_res)

    detail.prompt = new_prompt
    detail.options = new_options
    detail.resources = all_res
    return detail


def _publish_file_no_clobber(temp_file_path: str, target_path: Path) -> None:
    """原子发布临时文件到目标路径，绝不覆盖已有文件。

    使用 os.link 实现原子 no-clobber 写入：
    - 若目标路径已存在（或发布时竞态新建），os.link 抛出 FileExistsError，捕获并转换为 OutputExistsError。
    - 若系统或文件系统不支持硬链接等其他错误，真实抛出，严禁降级为会覆盖现有文件的 copy/replace。
    """
    try:
        os.link(temp_file_path, target_path)
    except FileExistsError as exc:
        raise OutputExistsError(f"输出文件已存在，拒绝覆盖: {target_path}") from exc


async def export_questions_to_markdown(
    platform: BasePlatform,
    course: Course,
    output_path: str | Path | None = None,
    download_resources: bool = False,
) -> ExportResult:
    """将课程题目导出为 Markdown 文件，使用临时文件原子保存，可选下载配图与附件。"""
    homeworks = await platform.get_homeworks(course)
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

                f.write(f"## {hw.name}\n\n")
                questions = hw_questions[hw.id]
                for idx, q in enumerate(questions, 1):
                    total_questions += 1
                    content = q.get("content", {})
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
        _publish_file_no_clobber(temp_file_path, target_path)
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


def _restore_images(prompt: str, images: list[str]) -> str:
    """把 `[图]` 占位按顺序还原为 Markdown 图片，便于随后本地化。"""
    for url in images:
        prompt = prompt.replace("[图]", f"![]({url})", 1)
    return prompt


async def export_exam_paper_to_markdown(
    platform: BasePlatform,
    course: Course,
    exam_id: str | int,
    output_path: str | Path,
    download_resources: bool = True,
) -> ExamExportResult:
    """导出考场试卷为 Markdown，可选下载题干与选项配图。"""
    paper = await platform.get_exam_paper(exam_id, course)
    if not paper.problems:
        raise RuntimeError("考场试卷为空，已中止导出")

    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        raise OutputExistsError(f"输出文件已存在，拒绝覆盖: {target_path}")

    assets_dir = target_path.parent / "assets" if download_resources else None
    if assets_dir:
        assets_dir.mkdir(parents=True, exist_ok=True)

    total_resources = 0
    temp_fd, temp_file_path = tempfile.mkstemp(prefix="wk_exam_", suffix=".md", dir=str(target_path.parent))
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(f"# {paper.title}\n\n")
            f.write(f"- 考试 ID：`{paper.exam_id}`\n- 题目数量：{len(paper.problems)}\n\n")

            for problem in paper.problems:
                prefix = f"exam_{paper.exam_id}_q_{problem.id}"
                body = _restore_images(problem.prompt, problem.images)
                if assets_dir:
                    body, body_res = await localize_text_resources(
                        platform.fetch_bytes, body, assets_dir, f"{prefix}_body", relative_base="./assets"
                    )
                    total_resources += len(body_res)

                f.write(f"## 第 {problem.index} 题（{problem.type or '题目'}，{problem.score:g} 分）\n\n")
                f.write(f"**题目：**\n{body}\n\n")

                if problem.options:
                    f.write("**选项：**\n")
                    for option in problem.options:
                        text = option.text
                        if assets_dir:
                            text, opt_res = await localize_text_resources(
                                platform.fetch_bytes,
                                text,
                                assets_dir,
                                f"{prefix}_opt_{_safe_filename_part(option.key)}",
                                relative_base="./assets",
                            )
                            total_resources += len(opt_res)
                        f.write(f"- **{option.key}**: {text}\n")
                    f.write("\n")

                if problem.answer:
                    f.write(f"**平台答案：** {'、'.join(problem.answer)}\n\n")
                f.write("---\n\n")
        _publish_file_no_clobber(temp_file_path, target_path)
    finally:
        if os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except OSError:
                pass

    return ExamExportResult(
        file_path=str(target_path),
        exam_id=paper.exam_id,
        problems_count=len(paper.problems),
        resources_count=total_resources,
    )
