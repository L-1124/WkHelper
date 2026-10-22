"""article 业务流程。"""

from wkhelper.domain.content import ArticleItem, ArticleReadResult
from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.parsing import format_leaf_label, iter_leaves_with_context


async def get_articles(platform: BasePlatform, course: Course) -> list[ArticleItem]:
    """获取课程资料/图文节点列表。"""
    data = await platform.get_chapter_data(course)
    schedules = await platform.get_leaf_schedules(course)
    articles: list[ArticleItem] = []
    for leaf, chapter_name, section_name in iter_leaves_with_context(data):
        if leaf.get("leaf_type") != 3 or not leaf.get("is_show", True):
            continue
        leaf_id = leaf.get("id")
        if leaf_id is None:
            continue
        label = format_leaf_label(leaf, chapter_name, section_name)
        is_completed = schedules.get(int(leaf_id), 0.0) >= 1.0
        articles.append(ArticleItem(id=str(leaf_id), name=label, completed=is_completed))
    return articles


async def read_article(platform: BasePlatform, leaf_id: str | int, course: Course) -> ArticleReadResult:
    """标记指定资料/图文节点为已读已完成。"""
    articles = await get_articles(platform, course)
    target = next((item for item in articles if item.id == str(leaf_id)), None)
    title = target.name if target else str(leaf_id)
    success = await platform.mark_article_finish(leaf_id, course)
    if not success:
        raise APIError(f"资料 {leaf_id} 标记完成失败，平台未确认成功", retryable=False)
    return ArticleReadResult(
        course_id=str(course.id),
        leaf_id=str(leaf_id),
        name=title,
        success=success,
        completed=True,
    )
