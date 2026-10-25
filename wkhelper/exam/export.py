"""考试导出功能。"""

import os
import tempfile
from pathlib import Path
from typing import Literal

from wkhelper.domain.courses import Course
from wkhelper.domain.errors import OutputExistsError
from wkhelper.domain.exam import ExamExportResult, ExamPaperData, ExamProblemItem
from wkhelper.exporting.resources import _safe_filename_part, localize_text_resources
from wkhelper.infra.files import publish_file_no_clobber
from wkhelper.infra.serialization import encode_json
from wkhelper.platform.base import BasePlatform

__all__ = ["export_exam_paper", "export_exam_paper_to_markdown"]


def _restore_images(prompt: str, images: list[str]) -> str:
    """把 `[图]` 占位按顺序还原为 Markdown 图片，便于随后本地化。"""
    for url in images:
        prompt = prompt.replace("[图]", f"![]({url})", 1)
    return prompt


async def export_exam_paper(
    platform: BasePlatform,
    course: Course,
    exam_id: str | int,
    output_path: str | Path,
    download_resources: bool = True,
    format: Literal["md", "markdown", "json"] | None = None,
) -> ExamExportResult:
    """导出考场试卷为 Markdown 或 JSON，可选下载题干与选项配图，支持后缀自动推断。"""
    paper = await platform.get_exam_paper(exam_id, course)
    if not paper.problems:
        raise RuntimeError("考场试卷为空，已中止导出")

    resolved_format = _resolve_exam_export_format(output_path, format)
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        raise OutputExistsError(f"输出文件已存在，拒绝覆盖: {target_path}")

    assets_dir = target_path.parent / "assets" if download_resources else None
    if assets_dir:
        assets_dir.mkdir(parents=True, exist_ok=True)

    temp_suffix = ".json" if resolved_format == "json" else ".md"
    temp_fd, temp_file_path = tempfile.mkstemp(prefix="wk_exam_", suffix=temp_suffix, dir=str(target_path.parent))
    try:
        if resolved_format == "json":
            total_resources = await _write_exam_json(temp_fd, platform, paper, assets_dir)
        else:
            total_resources = await _write_exam_markdown(temp_fd, platform, paper, assets_dir)
        publish_file_no_clobber(temp_file_path, target_path)
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


def _resolve_exam_export_format(output_path: str | Path, format: str | None) -> str:
    """根据显式参数或文件后缀确定考卷导出格式（默认为 md）。"""
    if format is not None:
        fmt = format.lower().strip()
        if fmt in ("json", "js"):
            return "json"
        if fmt in ("md", "markdown"):
            return "md"
        raise ValueError(f"不支持的导出格式: {format}，仅支持 md 或 json")
    suffix = Path(output_path).suffix.lower()
    if suffix == ".json":
        return "json"
    return "md"


async def _write_exam_markdown(
    temp_fd: int,
    platform: BasePlatform,
    paper: ExamPaperData,
    assets_dir: Path | None,
) -> int:
    """将考场试卷写入 Markdown 临时文件。"""
    total_resources = 0
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
            f.write(f"**题目 ID：** `{problem.id}`\n\n")
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
    return total_resources


async def _write_exam_json(
    temp_fd: int,
    platform: BasePlatform,
    paper: ExamPaperData,
    assets_dir: Path | None,
) -> int:
    """将考场试卷写入 JSON 临时文件，支持选项配图本地化。"""
    total_resources = 0
    exported_problems: list[ExamProblemItem] = []

    for problem in paper.problems:
        prefix = f"exam_{paper.exam_id}_q_{problem.id}"
        body = _restore_images(problem.prompt, problem.images)
        if assets_dir:
            body, body_res = await localize_text_resources(
                platform.fetch_bytes, body, assets_dir, f"{prefix}_body", relative_base="./assets"
            )
            total_resources += len(body_res)

        opts = []
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
            opts.append(option.model_copy(update={"text": text}))

        exported_problems.append(
            problem.model_copy(
                update={
                    "prompt": body,
                    "options": opts,
                }
            )
        )

    export_data = paper.model_copy(update={"problems": exported_problems})
    with os.fdopen(temp_fd, "wb") as f:
        f.write(encode_json(export_data))

    return total_resources


export_exam_paper_to_markdown = export_exam_paper
