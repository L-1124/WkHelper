"""叶节点类型分类与讨论状态来源测试。"""

from typing import Any

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.discussion.service import get_discussions
from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError
from wkhelper.domain.exam import ExamCoverDetail
from wkhelper.exam.service import get_exams
from wkhelper.homework.service import get_homeworks
from wkhelper.infra.http import AsyncSession
from wkhelper.video.service import get_videos


def _course() -> Course:
    return Course(id="1", name="测试课程", platform_id="test", metadata={"classroom_id": 2001, "university_id": 9})


class _LeafTreePlatform(FakeAsyncPlatform):
    """章节树覆盖前端全部叶类型：5=考试 6=作业 7=课件 9=随堂测验 10=公告 4=讨论。"""

    async def get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        return [
            {
                "name": "第一章",
                "leaf_list": [
                    {"id": 1, "name": "期中考试", "leaf_type": 5, "is_show": True},
                    {"id": 2, "name": "1.1作业", "leaf_type": 6, "is_show": True},
                    {"id": 3, "name": "课件", "leaf_type": 7, "is_show": True},
                    {"id": 4, "name": "随堂测验", "leaf_type": 9, "is_show": True},
                    {"id": 5, "name": "公告", "leaf_type": 10, "is_show": True},
                    {"id": 6, "name": "三件好事讨论", "leaf_type": 4, "is_show": True},
                    {"id": 7, "name": "线下课堂", "leaf_type": 8, "is_show": True},
                    {"id": 8, "name": "视频", "leaf_type": 0, "is_show": True},
                    {"id": 9, "name": "音频", "leaf_type": 1, "is_show": True},
                    {"id": 10, "name": "直播", "leaf_type": 2, "is_show": True},
                ],
            }
        ]

    async def get_leaf_schedules(self, course: Course) -> dict[int, float]:
        return {}

    async def get_exam_cover(self, leaf_id: str | int, course: Course) -> ExamCoverDetail:
        return ExamCoverDetail(
            course_id=str(course.id),
            exam_id=str(leaf_id),
            title="期中考试",
            problem_count=10,
            total_score=50.0,
            limit_minutes=0,
            start_time=None,
            deadline=None,
            status="未开始",
            description="",
            face_auth=False,
            capture_screen=False,
        )


@pytest.mark.asyncio
async def test_homework_and_exam_lists_split_by_leaf_type() -> None:
    """作业只收 leaf_type=6，考试只收 leaf_type=5，课件/测验/公告不得混入。"""
    async with AsyncSession() as client:
        platform = _LeafTreePlatform(client)
        course = _course()

        homeworks = await get_homeworks(platform, course)
        exams = await get_exams(platform, course)

    assert [hw.id for hw in homeworks] == ["2"]
    assert [exam.id for exam in exams] == ["1"]

    videos = await get_videos(platform, course)
    discussions = await get_discussions(platform, course)

    assert set(videos) == {8, 9, 10}  # 仅视频/音频/直播，线下课堂被排除
    assert [d.id for d in discussions] == ["6"]  # 公告不混入讨论


@pytest.mark.asyncio
async def test_homework_leaf_flows_to_exercise_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """leaf_type=6 是章节作业，必须走习题接口而不是被当成考试拒绝。"""
    async with AsyncSession() as client:
        platform = _LeafTreePlatform(client)
        calls: list[str] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            calls.append(url)
            if "leaf_info" in url:
                return {
                    "success": True,
                    "data": {"leaf_type": 6, "content_info": {"leaf_type_id": 9001}, "sku_id": 77},
                }
            return {"success": True, "data": {"problems": [{"problem_id": 1, "content": {}}]}}

        monkeypatch.setattr(platform, "_request_json", fake_request)
        monkeypatch.setattr(platform, "_build_leaf_info_url", lambda leaf_id, course: f"https://api.test/leaf_info/{leaf_id}")
        monkeypatch.setattr(
            platform, "_build_exercise_list_url", lambda leaf_type_id, sku_id=None: f"https://api.test/exercise/{leaf_type_id}/{sku_id}"
        )

        questions = await platform.get_leaf_questions("2", _course())

    assert len(questions) == 1
    assert calls[1] == "https://api.test/exercise/9001/77"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("leaf_type", "needle"),
    [(5, "是考试"), (7, "课件"), (9, "随堂测验"), (10, "公告")],
)
async def test_non_homework_leaf_rejected(monkeypatch: pytest.MonkeyPatch, leaf_type: int, needle: str) -> None:
    """非作业节点必须返回类型准确、可操作的错误，而不是空题目列表。"""
    async with AsyncSession() as client:
        platform = _LeafTreePlatform(client)

        async def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            return {"success": True, "data": {"leaf_type": leaf_type, "content_info": {"leaf_type_id": 1}}}

        monkeypatch.setattr(platform, "_request_json", fake_request)

        with pytest.raises(APIError, match=needle):
            await platform.get_leaf_questions("9", _course())


class _DiscussionStatusPlatform(_LeafTreePlatform):
    async def discussion_leaf_completed(self, leaf_id: str | int, course: Course) -> bool | None:
        return str(leaf_id) == "6"


@pytest.mark.asyncio
async def test_discussion_list_uses_comment_status() -> None:
    """讨论列表完成态以发言状态为准，章节进度为 0 也不得把已发言判成未完成。"""
    async with AsyncSession() as client:
        platform = _DiscussionStatusPlatform(client)
        discussions = await get_discussions(platform, _course())

    by_id = {item.id: item.completed for item in discussions}
    assert by_id["6"] is True


@pytest.mark.asyncio
async def test_discussion_list_falls_back_to_schedule() -> None:
    """平台无法判定发言状态时保留章节进度结论。"""
    async with AsyncSession() as client:
        platform = _LeafTreePlatform(client)
        discussions = await get_discussions(platform, _course())

    by_id = {item.id: item.completed for item in discussions}
    assert by_id["6"] is False
