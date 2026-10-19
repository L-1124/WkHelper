"""HTML 转 Markdown 的共享转换器。

`core` 内部多处需要把平台富文本降级为 Markdown（题目导出、讨论题详情），
单独成模块以避免 `exporter` 与 `base_platform` 互相导入。
"""

import html
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

        return super().convert_img(el, text, *args, **kwargs)  # type: ignore[misc]


def html_to_md(html_str: str) -> str:
    """把 HTML 片段转换为 Markdown 文本。"""
    if not html_str:
        return ""
    return WkhelperConverter().convert(html_str).strip()
