"""公告模块：节点过滤与正文解析测试。"""

from typing import Any

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.core.entities import Course
from wkhelper.core.exceptions import APIError
from wkhelper.core.transport import AsyncSession
from wkhelper.platform.yuketang import YuketangPlatform


def _course() -> Course:
    return Course(id="1", name="测试课程", platform_id="ykt", metadata={"classroom_id": 2001, "university_id": 9})


class _NoticeTreePlatform(FakeAsyncPlatform):
    async def _get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        return [
            {
                "name": "第一章",
                "leaf_list": [
                    {"id": 10, "name": "课程公告", "leaf_type": 10, "is_show": True},
                    {"id": 11, "name": "讨论区", "leaf_type": 4, "is_show": True},
                    {"id": 12, "name": "1.1作业", "leaf_type": 6, "is_show": True},
                ],
            }
        ]

    async def _get_leaf_schedules(self, course: Course) -> dict[int, float]:
        return {10: 1.0}


@pytest.mark.asyncio
async def test_notice_list_only_leaf_type_10() -> None:
    """公告列表只收 leaf_type=10，讨论与作业不得混入。"""
    async with AsyncSession() as client:
        platform = _NoticeTreePlatform(client)
        notices = await platform.get_notices(_course())
        discussions = await platform.get_discussions(_course())

    assert [notice.id for notice in notices] == ["10"]
    assert notices[0].completed is True
    assert [topic.id for topic in discussions] == ["11"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "leaf_info",
    [
        {"activity_id": 47645908, "classroom_id": 2001, "courseware_id": "cw1"},
        {"success": True, "data": {"activity_id": 47645908, "classroom_id": 2001, "courseware_id": "cw1"}},
    ],
    ids=["flat", "wrapped"],
)
async def test_yuketang_notice_detail_parses_content(monkeypatch: pytest.MonkeyPatch, leaf_info: dict[str, Any]) -> None:
    """公告正文经 leaf_level_info → activity_id → link/notice 读取并转 Markdown。"""
    async with AsyncSession() as client:
        platform = YuketangPlatform(client)

        async def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "leaf_level_info" in url:
                return leaf_info
            return {
                "errcode": 0,
                "data": {
                    "title": "期末安排",
                    "content": "<p>考试时间：<b>第 18 周</b></p>",
                    "publisher": "教务处",
                    "publish_time": 1792414421000,
                },
            }

        monkeypatch.setattr(platform, "_request_json", fake_request)
        detail = await platform.get_notice_detail("10", _course())

    assert detail.notice_id == 47645908
    assert detail.title == "期末安排"
    assert "第 18 周" in detail.content
    assert "**" in detail.content
    assert detail.publisher == "教务处"
    assert detail.published_at == 1792414421000


@pytest.mark.asyncio
async def test_yuketang_notice_detail_rejects_non_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少 activity_id 的节点必须给出明确失败。"""
    async with AsyncSession() as client:
        platform = YuketangPlatform(client)

        async def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            return {"activity_id": None}

        monkeypatch.setattr(platform, "_request_json", fake_request)

        with pytest.raises(APIError, match="不是公告"):
            await platform.get_notice_detail("11", _course())
