"""Core 域测试：Runner 调度行为与计划执行。"""

import tempfile
from pathlib import Path

import pytest

from wkhelper.core.entities import Course
from wkhelper.core.plan import VideoTarget
from wkhelper.core.results import VideoWatchResult
from wkhelper.core.video import VideoWatchOptions
from wkhelper.core.video_runner import VideoPlanRunner


@pytest.mark.asyncio
async def test_runner_execute_video_plan(fake_platform):
    """验证 Runner 计划并发执行与结果收集。"""
    runner = VideoPlanRunner(fake_platform)
    course = Course(id="c1", name="测试课程", platform_id="test")
    courses_map = {"c1": course}

    async def fake_do_video(
        video_id: str,
        video_name: str,
        course: Course,
        options: VideoWatchOptions | None = None,
    ) -> VideoWatchResult:
        return VideoWatchResult(
            video_id=video_id,
            video_name=video_name,
            status="completed",
            platform_completed=True,
            initial_rate=0.0,
            final_rate=1.0,
            heartbeats_sent=1,
            duration_seconds=1.5,
            message="已完成",
        )

    fake_platform.do_video = fake_do_video  # type: ignore[method-assign]

    targets = [
        VideoTarget(course_id="c1", video_id="v1", name="视频1"),
        VideoTarget(course_id="c1", video_id="v2", name="视频2"),
    ]

    with tempfile.TemporaryDirectory() as td:
        cp_file = Path(td) / "checkpoint.json"
        summary = await runner.execute_video_plan(
            courses_map=courses_map,
            targets=targets,
            concurrency=2,
            checkpoint_file=cp_file,
        )

        assert summary.total == 2
        assert summary.completed == 2
        assert summary.failed == 0
        assert len(summary.results) == 2
        assert cp_file.exists()


@pytest.mark.asyncio
async def test_runner_execute_video_plan_resume(fake_platform):
    """验证从检查点恢复时跳过已完成项。"""
    runner = VideoPlanRunner(fake_platform)
    course = Course(id="c1", name="测试课程", platform_id="test")
    courses_map = {"c1": course}

    called_videos: list[str] = []

    async def fake_do_video(
        video_id: str,
        video_name: str,
        course: Course,
        options: VideoWatchOptions | None = None,
    ) -> VideoWatchResult:
        called_videos.append(video_id)
        return VideoWatchResult(
            video_id=video_id,
            video_name=video_name,
            status="completed",
            platform_completed=True,
            initial_rate=0.0,
            final_rate=1.0,
            heartbeats_sent=1,
            duration_seconds=1.0,
            message="已完成",
        )

    fake_platform.do_video = fake_do_video  # type: ignore[method-assign]

    with tempfile.TemporaryDirectory() as td:
        cp_file = Path(td) / "checkpoint.json"
        cp_file.write_text(
            '{"results": [{"course_id": "c1", "video_id": "v1", "status": "completed", "final_rate": 1.0, "platform_completed": true}]}',
            encoding="utf-8",
        )

        targets = [
            VideoTarget(course_id="c1", video_id="v1", name="视频1"),
            VideoTarget(course_id="c1", video_id="v2", name="视频2"),
        ]

        summary = await runner.execute_video_plan(
            courses_map=courses_map,
            targets=targets,
            resume_from=cp_file,
        )

        # 视频1应该被跳过，只有视频2被调用
        assert called_videos == ["v2"]
        assert summary.completed == 2


@pytest.mark.asyncio
async def test_runner_execute_video_plan_total_timeout_cancellation(fake_platform):
    """验证总预算耗尽时取消已启动任务，并为已启动和未启动任务记录明确状态。"""
    import asyncio

    runner = VideoPlanRunner(fake_platform)
    course = Course(id="c1", name="测试课程", platform_id="test")
    courses_map = {"c1": course}

    async def hanging_do_video(
        video_id: str,
        video_name: str,
        course: Course,
        options: VideoWatchOptions | None = None,
    ) -> VideoWatchResult:
        await asyncio.sleep(5.0)
        return VideoWatchResult(video_id=video_id, video_name=video_name, status="completed")

    fake_platform.do_video = hanging_do_video  # type: ignore[method-assign]

    targets = [
        VideoTarget(course_id="c1", video_id="v1", name="视频1"),
        VideoTarget(course_id="c1", video_id="v2", name="视频2"),
    ]

    opts = VideoWatchOptions(total_timeout=0.05, video_delay=0.0)
    summary = await runner.execute_video_plan(
        courses_map=courses_map,
        targets=targets,
        concurrency=1,
        default_options=opts,
    )

    v1_res = next(r for r in summary.results if r.video_id == "v1")
    assert v1_res.status == "timed_out"
    assert "总时间预算耗尽" in v1_res.message

    v2_res = next(r for r in summary.results if r.video_id == "v2")
    assert v2_res.status == "stopped"


@pytest.mark.asyncio
async def test_runner_execute_video_plan_resume_respects_higher_threshold(fake_platform):
    """验证从检查点恢复时，提高阈值后 final_rate 低于新有效阈值的任务不得跳过。"""
    runner = VideoPlanRunner(fake_platform)
    course = Course(id="c1", name="测试课程", platform_id="test")
    courses_map = {"c1": course}

    called_videos: list[str] = []

    async def fake_do_video(
        video_id: str,
        video_name: str,
        course: Course,
        options: VideoWatchOptions | None = None,
    ) -> VideoWatchResult:
        called_videos.append(video_id)
        return VideoWatchResult(
            video_id=video_id,
            video_name=video_name,
            status="threshold_reached",
            platform_completed=False,
            initial_rate=0.6,
            final_rate=0.8,
            message="达到新阈值",
        )

    fake_platform.do_video = fake_do_video  # type: ignore[method-assign]

    with tempfile.TemporaryDirectory() as td:
        cp_file = Path(td) / "checkpoint.json"
        cp_file.write_text(
            '{"results": [{"course_id": "c1", "video_id": "v1", "status": "threshold_reached",'
            ' "final_rate": 0.6, "initial_rate": 0.0, "platform_completed": false}]}',
            encoding="utf-8",
        )

        targets = [
            VideoTarget(course_id="c1", video_id="v1", name="视频1"),
        ]

        # 场景 A：本次计划阈值提升到 0.8，0.6 < 0.8，不可跳过，必须重新调用
        opts_high = VideoWatchOptions(completion_threshold=0.8)
        called_videos.clear()
        await runner.execute_video_plan(
            courses_map=courses_map,
            targets=targets,
            resume_from=cp_file,
            default_options=opts_high,
        )
        assert called_videos == ["v1"], "提高阈值后历史进度未达标，必须重新执行"

        # 场景 B：本次计划阈值为 0.5，0.6 >= 0.5，满足本次阈值，应当跳过
        opts_low = VideoWatchOptions(completion_threshold=0.5)
        called_videos.clear()
        await runner.execute_video_plan(
            courses_map=courses_map,
            targets=targets,
            resume_from=cp_file,
            default_options=opts_low,
        )
        assert called_videos == [], "满足本次有效阈值时应直接恢复并跳过"
