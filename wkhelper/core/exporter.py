"""题目导出功能。"""

import asyncio
import html
import logging
import os
from typing import Any

from markdownify import MarkdownConverter

from wkhelper.core.config import MAX_WORKERS_DOWNLOAD
from wkhelper.core.db import db
from wkhelper.core.models import Course


class WkhelperConverter(MarkdownConverter):
    def convert_img(self, el, text, *args, **kwargs):
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
    # 去除多余的空行
    return WkhelperConverter().convert(html_str).strip()


logger = logging.getLogger(__name__)


async def export_questions_to_markdown(platform: Any, course: Course) -> None:
    """将课程题目导出为 Markdown 文件。"""
    logger.info(f"🔍 正在获取课程题目以导出: {course.name}")
    homeworks = await platform.get_homeworks(course)

    if not homeworks:
        logger.warning("⚠️ 该课程暂无作业可供导出")
        return

    logger.debug(f"📋 找到 {len(homeworks)} 个作业，准备导出")

    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    export_dir = os.path.join(base_dir, "exports")
    os.makedirs(export_dir, exist_ok=True)
    safe_course_name = "".join(c if c.isalnum() or c in " _-[]()" else "_" for c in course.name)
    file_path = os.path.join(export_dir, f"{safe_course_name}_题目.md")

    semaphore = asyncio.Semaphore(MAX_WORKERS_DOWNLOAD)

    # 获取所有题目
    hw_questions: dict[str, list[Any]] = {}

    async def _fetch(hw):
        try:
            async with semaphore:
                questions = await platform.get_leaf_questions(hw.id, course)
            hw_questions[hw.name] = questions
        except Exception as e:
            logger.error(f"  ❌ 获取作业 {hw.name} 题目失败: {e}")

    async with asyncio.TaskGroup() as tg:
        for hw in homeworks:
            tg.create_task(_fetch(hw))

    if not hw_questions:
        logger.warning("⚠️ 未能获取到任何题目")
        return

    # 写入 Markdown
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# {course.name} 题目汇总\n\n")

            for hw in homeworks:
                if hw.name not in hw_questions or not hw_questions[hw.name]:
                    continue

                f.write(f"## {hw.name}\n\n")

                questions = hw_questions[hw.name]
                for idx, q in enumerate(questions, 1):
                    content = q.get("content", {})
                    # 获取题干
                    body = content.get("Body") or content.get("body") or "未知题干"
                    body = html_to_md(body)
                    f.write(f"### 第 {idx} 题\n\n")
                    f.write(f"**题目：**\n{body}\n\n")

                    # 获取选项
                    options = content.get("Options") or []
                    if options:
                        f.write("**选项：**\n")
                        for opt in options:
                            key = opt.get("key", "")
                            value = html_to_md(opt.get("value", ""))
                            # 选项如果存在换行，增加适当缩进保持列表格式
                            value = value.replace("\n", "\n    ")
                            f.write(f"- **{key}**: {value}\n")
                        f.write("\n")

                    # 尝试从本地数据库获取正确答案
                    library_id = content.get("LibraryID") or content.get("library_id")
                    version = content.get("Version")
                    if library_id and version:
                        answer = db.get_answer(str(library_id), str(version))
                        if answer:
                            f.write(f"**正确答案：** `{', '.join(answer)}`\n\n")
                        else:
                            f.write("**正确答案：** 暂无\n\n")
                    else:
                        f.write("**正确答案：** 暂无 (无法获取题目 ID)\n\n")

                    f.write("---\n\n")

        logger.info(f"✅ 导出题目完成，文件已保存至：{file_path}")
    except Exception as e:
        logger.error(f"❌ 写入 Markdown 文件失败: {e}")
