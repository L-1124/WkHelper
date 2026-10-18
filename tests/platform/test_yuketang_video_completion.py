"""雨课堂视频完成判定与视频列表构建测试。"""

from typing import Any

import niquests
import pytest

from wkhelper.core.models import Course, UserInfo
from wkhelper.platform.yuketang import YuketangPlatform


def _course() -> Course:
    return Course(
        id=5279451,
        name="人工智能通识",
        platform_id="ykt",
        metadata={"classroom_id": 31322391, "university_id": 2930, "course_id": 5279451},
    )


@pytest.mark.asyncio
async def test_completed_video_ids_reads_watched_record(monkeypatch, fake_ui):
    """用 video-log/get_video_watched_record 判定完成：completed=1 或 rate 达标。"""
    async with niquests.AsyncSession() as client:
        platform = YuketangPlatform(client, fake_ui)
        platform.user = UserInfo(id=108178260, name="tester")

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            assert "get_video_watched_record" in url
            assert kwargs["params"]["cid"] == 5279451
            assert str(kwargs["params"]["classroom_id"]) == "31322391"
            return {
                "code": 0,
                "data": {
                    "91317672": {"completed": 1, "rate": 1.0},
                    "91317622": {"completed": 0, "rate": 0.2},
                    "91317623": {"completed": 0, "rate": 0.97},
                },
            }

        monkeypatch.setattr(platform, "_request_json", fake_request_json)

        assert await platform._get_completed_video_ids(_course()) == {91317672, 91317623}


@pytest.mark.asyncio
async def test_completed_video_ids_returns_none_on_error(monkeypatch, fake_ui):
    """接口异常时返回 None，让基类退回 leaf_schedules 判断，而不是宣称全部未完成。"""
    async with niquests.AsyncSession() as client:
        platform = YuketangPlatform(client, fake_ui)
        platform.user = UserInfo(id=1, name="tester")

        async def boom(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("500 Server Error")

        monkeypatch.setattr(platform, "_request_json", boom)
        assert await platform._get_completed_video_ids(_course()) is None


@pytest.mark.asyncio
async def test_build_videos_marks_completed_from_platform(monkeypatch, fake_ui):
    """有权威完成集合时，视频列表按它标记 [已完成]，且不再请求章节进度。"""
    async with niquests.AsyncSession() as client:
        platform = YuketangPlatform(client, fake_ui)
        course = _course()

        async def fake_chapter_data(course: Course) -> list[dict[str, Any]]:
            return [
                {
                    "name": "第一章",
                    "section_leaf_list": [
                        {
                            "name": "1.1",
                            "leaf_list": [
                                {"id": 11, "leaf_type": 0, "is_show": True, "name": "视频A"},
                                {"id": 12, "leaf_type": 0, "is_show": True, "name": "视频B"},
                            ],
                        }
                    ],
                }
            ]

        async def fake_completed(course: Course) -> set[int]:
            return {11}

        async def forbidden_schedules(course: Course) -> dict[int, float]:
            raise AssertionError("有权威完成集合时不应再请求章节进度")

        monkeypatch.setattr(platform, "_get_chapter_data", fake_chapter_data)
        monkeypatch.setattr(platform, "_get_completed_video_ids", fake_completed)
        monkeypatch.setattr(platform, "_get_leaf_schedules", forbidden_schedules)

        videos = await platform.get_videos(course)

        assert set(videos) == {11, 12}
        assert "[已完成]" in videos[11]
        assert "[已完成]" not in videos[12]
        assert course.metadata["completed_video_ids"] == {11}
