"""Core 域综合测试：作业处理与视频流程。"""

from typing import Any

import pytest

from wkhelper.core.entities import Course
from wkhelper.core.homework import generic_submit_homework
from wkhelper.core.results import QuestionSubmitOutcome
from wkhelper.core.transport import AsyncSession
from wkhelper.core.video import VideoWatchOptions, generic_watch_video


@pytest.mark.asyncio
async def test_generic_submit_homework_submits_and_returns_summary():
    """验证作业提交返回结构化总结，包含每题提交状态与正确性。"""

    async def fake_submit(
        problem_id: int,
        answer: list[str],
        course_info: Course,
        client: AsyncSession,
        kwargs: dict[str, Any] | None,
        question: dict[str, Any] | None = None,
    ) -> QuestionSubmitOutcome:
        if problem_id == 1:
            return QuestionSubmitOutcome(success=True, is_correct=True, correct_answer=["A"])
        return QuestionSubmitOutcome(success=True, is_correct=False, correct_answer=["C"])

    resolved_pairs: list[tuple[dict[str, Any], list[str]]] = [
        ({"id": 1, "content": {"LibraryID": "lib-1", "Version": "v1"}}, ["A"]),
        ({"id": 2, "content": {"LibraryID": "lib-2", "Version": "v2"}}, ["B"]),
    ]

    course = Course(id="1", name="测试课程", platform_id="test")
    async with AsyncSession() as client:
        summary = await generic_submit_homework(resolved_pairs, fake_submit, course, client)

    assert summary.total == 2
    assert summary.submitted_count == 2
    assert summary.correct_count == 1
    assert len(summary.items) == 2
    assert summary.items[0].is_correct is True
    assert summary.items[1].is_correct is False


@pytest.mark.asyncio
async def test_generic_submit_homework_already_submitted_and_platform_error():
    """验证已正确作答题目跳过提交，且平台返回 80001 时正确复用作答状态。"""

    called_ids: list[int] = []

    async def fake_submit(
        problem_id: int,
        answer: list[str],
        course_info: Course,
        client: AsyncSession,
        kwargs: dict[str, Any] | None,
        question: dict[str, Any] | None = None,
    ) -> QuestionSubmitOutcome:
        called_ids.append(problem_id)
        if problem_id == 2:
            return QuestionSubmitOutcome(
                success=False,
                is_correct=False,
                correct_answer=[],
                error="you do not have chance to answer (error_code: 80001)",
                error_code=80001,
            )
        return QuestionSubmitOutcome(
            success=False,
            is_correct=False,
            correct_answer=[],
            error="参数错误 (error_code: 40001)",
            error_code=40001,
        )

    resolved_pairs: list[tuple[dict[str, Any], list[str]]] = [
        ({"id": 1, "user": {"my_count": 1, "is_right": True, "answer": "A"}}, ["A"]),
        ({"id": 2, "user": {}}, ["B"]),
        ({"id": 3, "user": {}}, ["C"]),
    ]

    course = Course(id="1", name="测试课程", platform_id="test")
    async with AsyncSession() as client:
        summary = await generic_submit_homework(resolved_pairs, fake_submit, course, client)

    assert summary.total == 3
    assert 1 not in called_ids
    assert 2 in called_ids
    assert summary.submitted_count == 1
    assert summary.correct_count == 1
    assert summary.items[0].submitted is True
    assert summary.items[0].is_correct is True
    assert summary.items[1].submitted is False
    assert "80001" in str(summary.items[1].error)
    assert summary.items[2].submitted is False
    assert summary.items[2].error == "参数错误 (error_code: 40001)"


@pytest.mark.asyncio
async def test_generic_submit_homework_anti_risk_options():
    """验证作业提交支持自定义延迟、并发与乱序提交。"""
    order: list[int] = []

    async def fake_submit(
        problem_id: int,
        answer: list[str],
        course_info: Course,
        client: AsyncSession,
        kwargs: dict[str, Any] | None,
        question: dict[str, Any] | None = None,
    ) -> QuestionSubmitOutcome:
        order.append(problem_id)
        return QuestionSubmitOutcome(success=True, is_correct=True, correct_answer=answer)

    resolved_pairs: list[tuple[dict[str, Any], list[str]]] = [({"id": i}, ["A"]) for i in range(1, 6)]

    course = Course(id="1", name="测试课程", platform_id="test")
    async with AsyncSession() as client:
        summary = await generic_submit_homework(
            resolved_pairs,
            fake_submit,
            course,
            client,
            concurrency=1,
            delay=0.01,
            delay_jitter=0.005,
            random_order=True,
        )

    assert summary.total == 5
    assert summary.submitted_count == 5
    assert len(order) == 5
    # 无论实际提交执行顺序如何，返回 items 始终按原 index 排序
    assert [item.index for item in summary.items] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_generic_watch_video_threshold_reached():
    """验证视频达到设定阈值时正常退出并报告 threshold_reached。"""

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

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
        video_frame: float,
        seq: int,
        timestamp: int,
        cc: str,
        duration: float,
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
        status_code = 200
        headers: dict[str, str] = {}

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
        video_frame: float,
        seq: int,
        timestamp: int,
        cc: str,
        duration: float,
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


@pytest.mark.asyncio
async def test_heartbeat_batch_timestamps_cover_past_window():
    """批量心跳的 ts 必须落在刚刚过去的采样窗口，最后一点接近当前时刻。"""
    import time

    captured: dict[str, list[dict[str, Any]]] = {}

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def __init__(self, text: str) -> None:
            self.text = text

    class FakeSession:
        def __init__(self) -> None:
            self.get_count = 0

        async def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.get_count += 1
            rate = 0.2 if self.get_count == 1 else 0.95
            return FakeResponse(f'{{"data": {{"v1": {{"rate": {rate}, "watch_length": 100}}}}}}')

        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            captured.setdefault("batch", kwargs["json"]["heart_data"])
            return FakeResponse('{"success": true}')

    def fake_payload_gen(
        video_id: str,
        classroom_id: str,
        user_id: int,
        course_id: int,
        sku_id: int,
        video_frame: float,
        seq: int,
        timestamp: int,
        cc: str,
        duration: float,
    ) -> dict[str, Any]:
        return {"ts": timestamp, "cp": video_frame, "sq": seq}

    opts = VideoWatchOptions(
        learning_rate=1.0,
        heartbeat_interval=0.01,
        progress_interval=0.01,
        completion_threshold=0.9,
        video_timeout=5.0,
        sample_interval=5.0,
        batch_size=6,
    )

    before = int(time.time() * 1000)
    await generic_watch_video(
        client=FakeSession(),  # type: ignore[arg-type]
        video_id="v1",
        video_name="时间戳测试",
        classroom_id="c1",
        user_id=1,
        course_id=1,
        sku_id=1,
        progress_url="http://fake/progress",
        heartbeat_url="http://fake/heartbeat",
        payload_gen=fake_payload_gen,
        options=opts,
    )
    after = int(time.time() * 1000)

    stamps = [point["ts"] for point in captured["batch"]]
    assert stamps == sorted(stamps)
    assert stamps[-1] <= after
    assert stamps[-1] >= before - 1000
    assert stamps[-1] - stamps[0] == 25_000


@pytest.mark.asyncio
async def test_submit_respects_unlimited_retry_count():
    """user.count=0 表示不限次，已答但未对时必须允许再次提交。"""
    submitted: list[int] = []

    async def fake_submit(
        problem_id: int,
        answer: list[str],
        course_info: Course,
        client: AsyncSession,
        kwargs: dict[str, Any] | None,
        question: dict[str, Any] | None = None,
    ) -> QuestionSubmitOutcome:
        submitted.append(problem_id)
        return QuestionSubmitOutcome(success=True, is_correct=True, correct_answer=["A"])

    pairs = [
        (
            {"id": 7, "content": {"ProblemType": "1"}, "user": {"my_count": 3, "count": 0, "answer": ["B"], "is_correct": False}},
            ["A"],
        )
    ]
    course = Course(id="1", name="测试课程", platform_id="test")
    async with AsyncSession() as client:
        await generic_submit_homework(pairs, fake_submit, course, client)

    assert submitted == [7]


def test_judgement_answer_normalization() -> None:
    """判断题答案必须归一为平台约定的 true/false。"""
    from wkhelper.core.homework import normalize_judgement_answer

    question = {"content": {"ProblemType": "6"}}
    assert normalize_judgement_answer(question, ["A"]) == ["true"]
    assert normalize_judgement_answer(question, ["错"]) == ["false"]
    assert normalize_judgement_answer({"content": {"ProblemType": "1"}}, ["A"]) == ["A"]


@pytest.mark.asyncio
async def test_completed_video_sends_videoend_event():
    """平台确认完播后必须补发 et=videoend，供服务端消费完播事件。"""

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def __init__(self, text: str) -> None:
            self.text = text

    class FakeSession:
        def __init__(self) -> None:
            self.get_count = 0
            self.posts: list[list[dict[str, Any]]] = []

        async def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.get_count += 1
            rate = 0.2 if self.get_count == 1 else 1.0
            return FakeResponse(f'{{"data": {{"v1": {{"rate": {rate}, "watch_length": 100}}}}}}')

        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            self.posts.append(kwargs["json"]["heart_data"])
            return FakeResponse('{"success": true}')

    def fake_payload_gen(
        video_id: str,
        classroom_id: str,
        user_id: int,
        course_id: int,
        sku_id: int,
        video_frame: float,
        seq: int,
        timestamp: int,
        cc: str,
        duration: float,
    ) -> dict[str, Any]:
        return {"et": "heartbeat", "ts": timestamp}

    client = FakeSession()
    await generic_watch_video(
        client=client,  # type: ignore[arg-type]
        video_id="v1",
        video_name="完播测试",
        classroom_id="c1",
        user_id=1,
        course_id=1,
        sku_id=1,
        progress_url="http://fake/progress",
        heartbeat_url="http://fake/heartbeat",
        payload_gen=fake_payload_gen,
        options=VideoWatchOptions(
            learning_rate=1.0,
            heartbeat_interval=0.01,
            progress_interval=0.01,
            completion_threshold=0.9,
            video_timeout=5.0,
        ),
    )

    assert client.posts[-1][0]["et"] == "videoend"
