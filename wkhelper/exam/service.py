"""exam 业务流程。"""

import logging

from wkhelper.domain.courses import Course
from wkhelper.domain.exam import ExamItem
from wkhelper.domain.time_types import epoch_millis
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.parsing import format_leaf_label, iter_leaves_with_context

logger = logging.getLogger(__name__)


async def get_exams(platform: BasePlatform, course: Course) -> list[ExamItem]:
    """获取课程的期中/期末考试列表。"""
    data = await platform.get_chapter_data(course)
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
