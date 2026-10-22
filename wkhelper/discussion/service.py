"""discussion 业务流程。"""

import asyncio
import logging

from wkhelper.domain.content import DiscussionCommentResult, DiscussionItem, DiscussionTopicDetail
from wkhelper.domain.courses import Course
from wkhelper.exporting.markdown import html_to_md
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.parsing import format_leaf_label, iter_leaves_with_context

logger = logging.getLogger(__name__)

MAX_WORKERS_STATUS = 5


async def get_discussions(platform: BasePlatform, course: Course) -> list[DiscussionItem]:
    """获取课程讨论题列表。

    章节进度（`leaf_schedules`）与讨论页徽标（`comment/status`）是两个数据源，
    列表必须与徽标一致，否则已完成讨论会被反复要求发言。
    """
    data = await platform.get_chapter_data(course)
    schedules = await platform.get_leaf_schedules(course)
    discussions: list[DiscussionItem] = []
    for leaf, chapter_name, section_name in iter_leaves_with_context(data):
        if leaf.get("leaf_type") != 4 or not leaf.get("is_show", True):
            continue
        leaf_id = leaf.get("id")
        if leaf_id is None:
            continue
        label = format_leaf_label(leaf, chapter_name, section_name)
        is_completed = schedules.get(int(leaf_id), 0.0) >= 1.0
        discussions.append(DiscussionItem(id=str(leaf_id), name=label, completed=is_completed))

    pending = [item for item in discussions if not item.completed]
    if pending:
        semaphore = asyncio.Semaphore(MAX_WORKERS_STATUS)

        async def _status(item: DiscussionItem) -> bool | None:
            async with semaphore:
                return await platform.discussion_leaf_completed(item.id, course)

        statuses = await asyncio.gather(*(_status(item) for item in pending), return_exceptions=True)
        for item, status in zip(pending, statuses, strict=True):
            if isinstance(status, BaseException):
                logger.debug(f"讨论 {item.id} 发言状态查询失败，回退章节进度: {status}")
            elif status is True:
                item.completed = True
    return discussions


async def get_discussion_detail(platform: BasePlatform, leaf_id: str | int, course: Course) -> DiscussionTopicDetail:
    """获取讨论题详情与打卡状态。"""
    topic_info = await platform.fetch_discussion_unit(leaf_id, course)
    topic_id = int(topic_info["id"])
    user_comment_num = int(topic_info.get("user_comment_num") or 0)
    comment_count = int(topic_info.get("commented") or 0)
    content = topic_info.get("content")
    raw_text = str(content.get("text") or "") if isinstance(content, dict) else ""
    completed = await platform.discussion_completed(topic_info, course, leaf_id)

    discussions = await get_discussions(platform, course)
    target = next((item for item in discussions if item.id == str(leaf_id)), None)
    title = target.name if target else f"讨论题 {leaf_id}"

    return DiscussionTopicDetail(
        course_id=str(course.id),
        leaf_id=str(leaf_id),
        topic_id=topic_id,
        title=title,
        prompt=html_to_md(raw_text),
        comment_count=comment_count,
        user_comment_num=user_comment_num,
        completed=completed,
    )


async def submit_discussion(platform: BasePlatform, leaf_id: str | int, course: Course, content: str) -> DiscussionCommentResult:
    """提交讨论题发言并返回打卡结果。"""
    topic_info = await platform.fetch_discussion_unit(leaf_id, course)
    topic_id = int(topic_info["id"])
    parsed = await platform.post_discussion_comment(topic_info, content, course, leaf_id)
    data = parsed.get("data")
    nested = data.get("data") if isinstance(data, dict) else None
    msg = parsed.get("msg") or (data.get("message") if isinstance(data, dict) else None) or "操作成功"
    comment_id = nested.get("id") if isinstance(nested, dict) else None
    return DiscussionCommentResult(
        course_id=str(course.id),
        leaf_id=str(leaf_id),
        topic_id=topic_id,
        comment_id=int(comment_id) if comment_id else None,
        success=bool(parsed.get("success", False)),
        message=str(msg),
    )
