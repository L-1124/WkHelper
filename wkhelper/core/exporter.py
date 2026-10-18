"""题目导出功能。"""

import asyncio
import html
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from markdownify import MarkdownConverter

from wkhelper.core.config import MAX_WORKERS_DOWNLOAD
from wkhelper.core.exceptions import OutputExistsError
from wkhelper.core.models import Course, ExportResult, Homework
from wkhelper.platform.base import BasePlatform

logger = logging.getLogger(__name__)


class WkhelperConverter(MarkdownConverter):
    def convert_img(self, el: Any, text: str, *args: Any, **kwargs: Any) -> str:
        latex = el.get("data-latex")
        if latex:
            latex_code = html.unescape(latex)
            if el.get("data-display") == "block":
                return f"$${latex_code}$$"
            return f"${latex_code}$"
        return super().convert_img(el, text, *args, **kwargs)  # type: ignore[misc]


def html_to_md(html_str: str) -> str:
    if not html_str:
        return ""
    return WkhelperConverter().convert(html_str).strip()


async def export_questions_to_markdown(
    platform: BasePlatform,
    course: Course,
    output_path: str | Path | None = None,
) -> ExportResult:
    """将课程题目导出为 Markdown 文件，使用临时文件原子保存。"""
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
    hw_questions: dict[str, list[dict[str, Any]]] = {}

    async def _fetch(hw: Homework) -> None:
        try:
            async with semaphore:
                questions = await platform.get_leaf_questions(hw.id, course)
            hw_questions[hw.name] = questions
        except Exception as e:
            logger.debug(f"获取作业 {hw.name} 题目失败: {e}")

    async with asyncio.TaskGroup() as tg:
        for hw in homeworks:
            tg.create_task(_fetch(hw))

    if not hw_questions or not any(hw_questions.values()):
        raise RuntimeError("未能从任何作业中成功抓取题目，已中止导出以防覆盖已有文件")

    total_questions = 0
    temp_fd, temp_file_path = tempfile.mkstemp(prefix="wk_export_", suffix=".md", dir=str(target_path.parent))
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(f"# {course.name} 题目汇总\n\n")

            for hw in homeworks:
                if hw.name not in hw_questions or not hw_questions[hw.name]:
                    continue

                f.write(f"## {hw.name}\n\n")
                questions = hw_questions[hw.name]
                for idx, q in enumerate(questions, 1):
                    total_questions += 1
                    content = q.get("content", {})
                    body = content.get("Body") or content.get("body") or "未知题干"
                    body = html_to_md(body)
                    f.write(f"### 第 {idx} 题\n\n")
                    f.write(f"**题目：**\n{body}\n\n")

                    options = content.get("Options") or []
                    if options:
                        f.write("**选项：**\n")
                        for opt in options:
                            key = opt.get("key", "")
                            value = html_to_md(opt.get("value", ""))
                            value = value.replace("\n", "\n    ")
                            f.write(f"- **{key}**: {value}\n")
                        f.write("\n")

                    f.write("---\n\n")

        os.replace(temp_file_path, target_path)
    except Exception:
        if os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except OSError:
                pass
        raise

    return ExportResult(
        file_path=str(target_path),
        homeworks_count=len(hw_questions),
        questions_count=total_questions,
    )
