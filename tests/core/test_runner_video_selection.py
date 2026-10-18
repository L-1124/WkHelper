"""视频选择器默认勾选未完成视频测试。"""

from typing import Any

import pytest

from wkhelper.core.models import Course
from wkhelper.core.runner import Runner


@pytest.mark.asyncio
async def test_batch_learn_videos_preselects_pending_videos(fake_platform, monkeypatch):
    """未完成的视频默认勾选，已完成的仍保持不可选。"""
    course = Course(
        id=1,
        name="课程1",
        platform_id="test",
        metadata={"classroom_id": 1, "completed_video_ids": {11}},
    )

    async def fake_get_videos(_course: Course) -> dict[int, str]:
        return {11: "视频A [已完成]", 12: "视频B", 13: "视频C"}

    calls: dict[str, Any] = {}

    async def fake_select_many(
        message: str,
        choices: list[str],
        default_selected: set[str] | None = None,
        disabled_choices: set[str] | None = None,
    ) -> list[str]:
        calls["message"] = message
        calls["choices"] = choices
        calls["default_selected"] = default_selected
        calls["disabled_choices"] = disabled_choices
        return []

    monkeypatch.setattr(fake_platform, "get_videos", fake_get_videos)
    monkeypatch.setattr(fake_platform.ui, "select_many", fake_select_many)

    await Runner(fake_platform).batch_learn_videos([course])

    assert calls["default_selected"] == {"视频B", "视频C"}
    assert calls["disabled_choices"] == {"视频A [已完成]"}
