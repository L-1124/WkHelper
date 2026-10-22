"""考试列表状态来源测试。"""

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.domain.courses import Course
from wkhelper.domain.exam import ExamCoverDetail
from wkhelper.exam.service import get_exams
from wkhelper.infra.http import AsyncSession


class _ExamListPlatform(FakeAsyncPlatform):
    """章节树里带一个考试节点，封面返回已交卷。"""

    async def get_chapter_data(self, course: Course) -> list[dict]:
        return [{"name": "第一章", "leaf_list": [{"id": 1, "name": "期末考试", "leaf_type": 5}]}]

    async def get_exam_cover(self, leaf_id: str | int, course: Course) -> ExamCoverDetail:
        return ExamCoverDetail(
            course_id=str(course.id),
            exam_id=str(leaf_id),
            title="期末考试",
            problem_count=10,
            total_score=50.0,
            limit_minutes=0,
            start_time=None,
            deadline=None,
            status="已交卷",
            description="",
            face_auth=False,
            capture_screen=False,
        )


@pytest.mark.asyncio
async def test_exam_list_uses_cover_status() -> None:
    """考试列表以考场封面状态为准，避免与章节进度不一致。"""
    async with AsyncSession() as client:
        platform = _ExamListPlatform(client)
        course = Course(id="1", name="测试课程1", platform_id="test")
        exams = await get_exams(platform, course)

    assert len(exams) == 1
    assert exams[0].status == "已交卷"
    assert exams[0].completed is True
