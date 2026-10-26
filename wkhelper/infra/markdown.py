"""HTML 转 Markdown 的通用基础设施。

供导出、讨论区及平台层解析富文本题目与公告时降级为 Markdown，
作为无状态文本转换基础设施独立存在，避免跨层循环依赖。
"""

import html
import re
from typing import Any

from markdownify import MarkdownConverter


class WkhelperConverter(MarkdownConverter):
    """把公式图片还原为 LaTeX，其余元素交给 markdownify 默认处理。"""

    def convert_img(self, el: Any, text: str, *args: Any, **kwargs: Any) -> str:
        latex = el.get("data-latex") or el.get("data-formula") or el.get("data-math") or el.get("data-tex") or el.get("math-latex")
        if not latex:
            alt = str(el.get("alt") or "").strip()
            if any(c in alt for c in ("\\", "$", "^", "_", "{", "}")) and not alt.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
            ):
                latex = alt

        if latex:
            latex_code = html.unescape(latex).strip()
            if (latex_code.startswith("$$") and latex_code.endswith("$$")) or (latex_code.startswith("$") and latex_code.endswith("$")):
                return latex_code
            is_block = el.get("data-display") == "block" or "display" in str(el.get("class", ""))
            return f"$${latex_code}$$" if is_block else f"${latex_code}$"

        # markdownify 存根未声明运行时存在的 convert_img 方法
        return super().convert_img(el, text, *args, **kwargs)  # type: ignore[misc]  # ty: ignore[unresolved-attribute]


def html_to_md(html_str: str) -> str:
    """把 HTML 片段转换为 Markdown 文本。"""
    if not html_str:
        return ""
    return WkhelperConverter().convert(html_str).strip()


_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_IMG_SRC = re.compile(r"""src\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def html_to_md_with_images(html_str: str) -> tuple[str, list[str]]:
    """转换富文本：非公式图片抽出地址列表，正文内保留 `[图]` 占位。"""
    if not html_str:
        return "", []

    images: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        if re.search(r"data-(latex|formula|math|tex)", tag, re.IGNORECASE):
            return tag
        src = _IMG_SRC.search(tag)
        if not src:
            return ""
        images.append(html.unescape(src.group(1)))
        return "[图]"

    return html_to_md(_IMG_TAG.sub(_replace, html_str)), images
