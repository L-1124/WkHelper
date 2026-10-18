"""Core 域综合测试：作业处理与视频流程。"""

from typing import Any

import niquests
import pytest

from wkhelper.core.homework import generic_submit_homework
from wkhelper.core.models import Course, QuestionSubmitOutcome
from wkhelper.core.video import VideoWatchOptions, generic_watch_video


@pytest.mark.asyncio
async def test_generic_submit_homework_submits_and_returns_summary():
    """验证作业提交返回结构化总结，包含每题提交状态与正确性。"""

    async def fake_submit(
        problem_id: int,
        answer: list[str],
        course_info: Course,
        client: niquests.AsyncSession,
        kwargs: dict[str, Any] | None,
    ) -> QuestionSubmitOutcome:
        if problem_id == 1:
            return QuestionSubmitOutcome(success=True, is_correct=True, correct_answer=["A"])
        return QuestionSubmitOutcome(success=True, is_correct=False, correct_answer=["C"])

    resolved_pairs: list[tuple[dict[str, Any], list[str]]] = [
        ({"id": 1, "content": {"LibraryID": "lib-1", "Version": "v1"}}, ["A"]),
        ({"id": 2, "content": {"LibraryID": "lib-2", "Version": "v2"}}, ["B"]),
    ]

    course = Course(id="1", name="测试课程", platform_id="test")
    async with niquests.AsyncSession() as client:
        summary = await generic_submit_homework(resolved_pairs, fake_submit, course, client)

    assert summary.total == 2
    assert summary.submitted_count == 2
    assert summary.correct_count == 1
    assert len(summary.items) == 2
    assert summary.items[0].is_correct is True
    assert summary.items[1].is_correct is False


@pytest.mark.asyncio
async def test_generic_watch_video_threshold_reached():
    """验证视频达到设定阈值时正常退出并报告 threshold_reached。"""

    class FakeResponse:
        def __init__(self, text: str):
            self.text = text

    class FakeSession:
        def __init__(self):
            self.get_count = 0

        async def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.get_count += 1
            rate = 0.5 if self.get_count == 1 else 0.95
            return FakeResponse(f'{{"data": {{"v1": {{"rate": {rate}, "watch_length": 100}}}}}}')

        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            return FakeResponse('{"success": true}')

    def fake_payload_gen(
        video_id: str,
        classroom_id: str,
        user_id: int,
        course_id: int,
        sku_id: int,
        video_frame: int,
        i: int,
        timestamp: int,
    ) -> dict[str, Any]:
        return {}

    opts = VideoWatchOptions(
        learning_rate=1.0,
        heartbeat_interval=0.01,
        progress_interval=0.01,
        completion_threshold=0.9,
        video_timeout=5.0,
    )

    client = FakeSession()
    res = await generic_watch_video(
        client=client,  # type: ignore[arg-type]
        video_id="v1",
        video_name="测试视频",
        classroom_id="c1",
        user_id=1,
        course_id=1,
        sku_id=1,
        progress_url="http://fake/progress",
        heartbeat_url="http://fake/heartbeat",
        payload_gen=fake_payload_gen,
        options=opts,
    )

    assert res.status in ("threshold_reached", "completed")
    assert res.final_rate >= 0.9
    assert res.video_id == "v1"


@pytest.mark.asyncio
async def test_generic_watch_video_stalled():
    """验证服务端进度停滞时触发 stall_timeout 停止。"""

    class FakeResponse:
        def __init__(self, text: str):
            self.text = text

    class FakeSession:
        async def get(self, url: str, **kwargs: Any) -> FakeResponse:
            return FakeResponse('{"data": {"v1": {"rate": 0.1, "watch_length": 10}}}')

        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            return FakeResponse('{"success": true}')

    def fake_payload_gen(
        video_id: str,
        classroom_id: str,
        user_id: int,
        course_id: int,
        sku_id: int,
        video_frame: int,
        i: int,
        timestamp: int,
    ) -> dict[str, Any]:
        return {}

    opts = VideoWatchOptions(
        learning_rate=1.0,
        heartbeat_interval=0.01,
        progress_interval=0.01,
        completion_threshold=1.0,
        stall_timeout=0.05,
        video_timeout=5.0,
    )

    client = FakeSession()
    res = await generic_watch_video(
        client=client,  # type: ignore[arg-type]
        video_id="v1",
        video_name="停滞视频",
        classroom_id="c1",
        user_id=1,
        course_id=1,
        sku_id=1,
        progress_url="http://fake/progress",
        heartbeat_url="http://fake/heartbeat",
        payload_gen=fake_payload_gen,
        options=opts,
    )

    assert res.status == "stalled"
    assert "无变化" in res.message
