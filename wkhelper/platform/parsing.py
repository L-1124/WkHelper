"""平台章节及习题结构解析。"""

import logging
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticUseDefault

logger = logging.getLogger(__name__)

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
    if section_name and section_name not in (chapter_name, raw_name):
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
                    sub_children = sub.get("section_leaf_list") or sub.get("section_list") or sub.get("leaf_list") or sub.get("units")
                    section_name = sub.get("name") if isinstance(sub_children, list) and sub_children else None
                    yield from _traverse(sub, ch_name, section_name)
        elif "leaf_type" in root_item:
            yield root_item, ch_name, None


def parse_leaf_schedules(raw: Any) -> dict[int, float]:
    """把 `leaf_schedules` 原始映射解析为 int 叶子 ID -> 进度。"""
    if not isinstance(raw, dict):
        return {}
    schedules: dict[int, float] = {}
    for leaf_id, progress in raw.items():
        try:
            schedules[int(leaf_id)] = float(progress)
        except (TypeError, ValueError):
            continue
    return schedules


def parse_exercise_response(q_resp: dict[str, Any]) -> list[dict[str, Any]]:
    """解析习题 API 响应中的题目列表，兼容多种响应结构。"""
    if q_resp.get("success") is False:
        error_msg = q_resp.get("msg", "Unknown error")
        logger.warning(f"API 返回错误: {error_msg}")
        return []

    data = q_resp.get("data", q_resp)
    if isinstance(data, dict):
        if "problems" in data:
            return data["problems"]
    if isinstance(data, list):
        return data
    return []


class PlatformCounters(BaseModel):
    """平台计数字段的宽松兼容解析；异常值沿用原字段默认值。"""

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    pages: int = 1
    max_screen_cuts_num: int = 0
    max_retry: int = 1
    activity_id: int = 0
    my_count: int = 0

    @field_validator("*", mode="before")
    @classmethod
    def normalize_count(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise PydanticUseDefault()
        try:
            return int(value)
        except (ValueError, OverflowError):
            raise PydanticUseDefault() from None
