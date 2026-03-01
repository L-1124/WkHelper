"""Core 域综合测试：Runner 调度行为。"""

import asyncio
from typing import Any

import pytest

from wkhelper.core.models import Course, Homework
from wkhelper.core.runner import Runner


@pytest.mark.asyncio
async def test_runner_parallel_execution(fake_platform):
    """验证 Runner 并发调度并记录成功数。"""
    runner = Runner(fake_platform)
    done_count = 0

    async def mock_task():
        nonlocal done_count
        await asyncio.sleep(0.01)
        done_count += 1

    await runner._run_parallel_tasks(
        "测试任务",
        [("任务1", mock_task), ("任务2", mock_task)],
        max_workers=2,
    )
    assert done_count == 2


@pytest.mark.asyncio
async def test_auto_homework_matches_same_lecture_not_cross_chapter(fake_platform):
    """验证自动作业按“同讲次且不跨章”匹配。"""
    runner = Runner(fake_platform)
    course = Course(id=1, name="测试课程", platform_id="test")

    homeworks = [
        Homework(
            id=1,
            name="第五章 向量代数与空间解析几何 › 第一讲 向量及其线性运算--作业",
            metadata={"is_completed": False},
        ),
        Homework(
            id=2,
            name="第六章 多元函数微分法及其应用 › 第一讲 多元函数的基本概念--作业",
            metadata={"is_completed": False},
        ),
    ]

    async def fake_get_homeworks(_course: Course) -> list[Homework]:
        return homeworks

    captured: dict[str, Any] = {"names": []}

    async def fake_run_parallel_tasks(
        _title: str,
        items: list[tuple[str, Any]],
        _max_workers: int,
    ) -> None:
        captured["names"] = [name for name, _ in items]

    fake_platform.get_homeworks = fake_get_homeworks  # type: ignore[method-assign]
    runner._run_parallel_tasks = fake_run_parallel_tasks  # type: ignore[method-assign]

    await runner._auto_do_homework_by_videos(
        course,
        ["第五章 向量代数与空间解析几何 › 第一讲 向量及其线性运算 › 视频A"],
    )

    assert captured["names"] == ["第五章 向量代数与空间解析几何 › 第一讲 向量及其线性运算--作业"]
