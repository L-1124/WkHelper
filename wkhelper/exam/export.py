"""考试导出功能。"""

import os
import tempfile
from pathlib import Path

from wkhelper.domain.courses import Course
from wkhelper.domain.errors import OutputExistsError
from wkhelper.domain.exam import ExamExportResult
from wkhelper.exporting.resources import _safe_filename_part, localize_text_resources
from wkhelper.infra.files import publish_file_no_clobber
from wkhelper.platform.base import BasePlatform

__all__ = ["export_exam_paper_to_markdown"]


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
