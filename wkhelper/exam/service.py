"""exam 业务流程。"""

import logging
from pathlib import Path
from typing import Any

from wkhelper.domain.courses import Course
from wkhelper.domain.exam import ExamItem
from wkhelper.domain.time_types import epoch_millis
from wkhelper.exam.session import ExamBrowserSession, browser_cookies, load_session
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.parsing import format_leaf_label, iter_leaves_with_context

logger = logging.getLogger(__name__)


async def bind_browser_session(platform: BasePlatform, exam: str | int, *, data_dir: str | Path | None = None) -> ExamBrowserSession | None:
    """只复用当前平台、登录用户及考试节点对应的浏览器会话。"""
    session = load_session(data_dir)
    if (
        session is None
        or session.exam_id != str(exam)
        or session.platform != platform.platform_code
        or platform.user is None
        or session.account_id != str(platform.user.id)
    ):
        return None
    cookies = await browser_cookies(session)
    for name, value in cookies.items():
        platform.client.cookies.set(name, value)
    platform.bind_exam_room(session.host, session.eid, session.web_url, leaf_id=exam)
    return session


async def get_exams(
    platform: BasePlatform,
    course: Course,
    *,
    chapter_data: list[dict[str, Any]] | None = None,
    schedules: dict[int, float] | None = None,
) -> list[ExamItem]:
    """获取考试列表；课程聚合可复用同一份章节与进度快照。"""
    data = await platform.get_chapter_data(course) if chapter_data is None else chapter_data
    if schedules is None:
        schedules = await platform.get_leaf_schedules(course)
    exams: list[ExamItem] = []
    for leaf, chapter_name, section_name in iter_leaves_with_context(data):
        # 只有 leaf_type=5 才进入考场子系统；9=随堂测验属于活动流，不走考场接口
        if leaf.get("leaf_type") != 5 or not leaf.get("is_show", True):
            continue
        leaf_id = leaf.get("id")
        if leaf_id is None:
            continue
        is_completed = schedules.get(int(leaf_id), 0.0) >= 1.0
        deadline = epoch_millis(leaf.get("score_deadline") or leaf.get("end_time"))
        label = format_leaf_label(leaf, chapter_name, section_name)
        exams.append(
            ExamItem(
                id=str(leaf_id),
                name=label,
                start_time=epoch_millis(leaf.get("start_time")),
                deadline=deadline,
                completed=is_completed,
            )
        )

    for exam in exams:
        try:
            cover = await platform.get_exam_cover(exam.id, course)
        except Exception as exc:  # 单个考试封面失败不影响整体列表
            logger.debug(f"考试 {exam.id} 封面查询失败: {exc}")
            continue
        exam.status = cover.status
        if cover.status == "已交卷":
            exam.completed = True
        elif cover.status == "已缺考":
            exam.overdue = True
    return exams
