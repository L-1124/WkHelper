"""Core 域测试：Runner 调度行为与计划执行。"""

import tempfile
from pathlib import Path

import pytest

from wkhelper.core.models import Course, VideoTarget, VideoWatchResult
from wkhelper.core.runner import Runner
from wkhelper.core.video import VideoWatchOptions


@pytest.mark.asyncio
async def test_runner_execute_video_plan(fake_platform):
    """验证 Runner 计划并发执行与结果收集。"""
    runner = Runner(fake_platform)
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
    runner = Runner(fake_platform)
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
            '{"results": [{"course_id": "c1", "video_id": "v1", "status": "completed"}]}',
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
