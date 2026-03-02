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
    return f"{' › '.join(parts)} (ID:{leaf.get('id')})"


def iter_leaves_with_context(chapter_data: list[dict[str, Any]]) -> Iterator[LeafContext]:
    """遍历课件叶子节点并保留章节上下文。"""
    for chapter in chapter_data:
        chapter_name = str(chapter.get("name") or "未命名章节")
        for section in chapter.get("section_leaf_list", []):
            section_name = section.get("name")
            leaf_list = section.get("leaf_list")
            if isinstance(leaf_list, list) and leaf_list:
                for leaf in leaf_list:
                    yield leaf, chapter_name, section_name
            else:
                # section 本身就是叶子（常见于直接挂载作业或复习视频）
                yield section, chapter_name, None
