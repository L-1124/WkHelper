"""平台章节树通用工具。"""

from collections.abc import Iterator
from typing import Any

type LeafContext = tuple[dict[str, Any], str, str | None]


def format_leaf_label(
    leaf: dict[str, Any],
    chapter_name: str,
    section_name: str | None,
) -> str:
    """构建树形展示标签，避免同名任务导致选择歧义。"""
    raw_name = str(leaf.get("name") or "未命名任务").strip()
    if raw_name.lower() == "video" and section_name:
        raw_name = section_name

    parts = [chapter_name]
    if section_name and section_name != chapter_name:
        parts.append(section_name)
    parts.append(raw_name)
    return f"{' / '.join(parts)} (ID:{leaf.get('id')})"


def iter_leaves_with_context(chapter_data: list[dict[str, Any]]) -> Iterator[LeafContext]:
    """递归遍历课件树，支持单层、多层嵌套及混合结构，保留完整章节上下文。"""

    def _traverse(node: dict[str, Any], chapter: str, section: str | None) -> Iterator[LeafContext]:
        name = str(node.get("name") or "").strip()
        children = node.get("section_leaf_list") or node.get("section_list") or node.get("leaf_list") or node.get("units")

        if isinstance(children, list) and children:
            curr_chapter = chapter or name or "未命名章节"
            curr_section = name if chapter else section
            for child in children:
                if isinstance(child, dict):
                    yield from _traverse(child, curr_chapter, curr_section)
        else:
            yield node, chapter or "未命名章节", section

    for root_item in chapter_data:
        if not isinstance(root_item, dict):
            continue
        ch_name = str(root_item.get("name") or "未命名章节")
        children = (
            root_item.get("section_leaf_list") or root_item.get("section_list") or root_item.get("leaf_list") or root_item.get("units")
        )
        if isinstance(children, list) and children:
            for sub in children:
                if isinstance(sub, dict):
                    yield from _traverse(sub, ch_name, sub.get("name"))
        elif "leaf_type" in root_item:
            yield root_item, ch_name, None
