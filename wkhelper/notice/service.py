"""notice 业务流程。"""

from wkhelper.domain.content import NoticeItem
from wkhelper.domain.courses import Course
from wkhelper.domain.time_types import epoch_millis
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.parsing import format_leaf_label, iter_leaves_with_context


async def get_notices(platform: BasePlatform, course: Course) -> list[NoticeItem]:
    """获取课程公告/通知节点列表（`leaf_type=10`）。"""
    data = await platform.get_chapter_data(course)
    schedules = await platform.get_leaf_schedules(course)
    notices: list[NoticeItem] = []
    for leaf, chapter_name, section_name in iter_leaves_with_context(data):
        if leaf.get("leaf_type") != 10 or not leaf.get("is_show", True):
            continue
        leaf_id = leaf.get("id")
        if leaf_id is None:
            continue
        notices.append(
            NoticeItem(
                id=str(leaf_id),
                name=format_leaf_label(leaf, chapter_name, section_name),
                completed=schedules.get(int(leaf_id), 0.0) >= 1.0,
                published_at=epoch_millis(leaf.get("start_time")),
            )
        )
    return notices
