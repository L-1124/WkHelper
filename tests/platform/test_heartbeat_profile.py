"""心跳报文的平台差异契约测试。"""

import niquests
import pytest

from wkhelper.core.base_platform import BasePlatform, HeartbeatProfile
from wkhelper.core.json_types import JsonDict
from wkhelper.platform.xuetangx import XuetangXPlatform
from wkhelper.platform.yuketang import YuketangPlatform


def _build(platform: BasePlatform) -> JsonDict:
    return platform._build_heartbeat_payload(
        video_id="1001",
        classroom_id="2001",
        user_id=7,
        course_id=8,
        sku_id=9,
        video_frame=120,
        i=3,
        timestamp=1700000000,
    )


@pytest.mark.asyncio
async def test_heartbeat_payload_uses_platform_profile() -> None:
    """心跳报文中的平台固定字段来自 `heartbeat_profile`，而非 core 硬编码。"""
    async with niquests.AsyncSession() as client:
        for platform in (YuketangPlatform(client), XuetangXPlatform(client)):
            profile = platform.heartbeat_profile
            payload = _build(platform)

            assert payload["n"] == profile.origin
            assert payload["lob"] == profile.lob
            assert payload["d"] == profile.fixed_duration
            assert payload["sq"] == 3
            assert payload["cc"] == "1001"


def test_heartbeat_profile_is_required() -> None:
    """未声明心跳档案的平台无法实例化，避免再次出现静默继承。"""

    class IncompletePlatform(BasePlatform):
        def _login_with_cookies(self, cookies):  # noqa: ANN001, ANN202
            raise NotImplementedError

        async def _login_with_qrcode(self, display="image", open_browser=False, timeout=120):
            raise NotImplementedError

        async def get_courses(self):
            return []

        async def _get_chapter_data(self, course):
            return []

        async def _get_leaf_schedules(self, course):
            return {}

        def _build_leaf_info_url(self, leaf_id, course):
            return ""

        def _build_exercise_list_url(self, leaf_type_id):
            return ""

        def _build_submit_url(self):
            return ""

        def _build_submit_payload(self, problem_id, answer, submit_ctx=None):
            return {}

        async def _prepare_video_context(self, video_id, course):
            raise NotImplementedError

        async def _prepare_submit_context(self, homework, course):
            return {}

    assert issubclass(IncompletePlatform, BasePlatform)
    with pytest.raises(TypeError):
        IncompletePlatform(None)  # type: ignore[arg-type]

    assert HeartbeatProfile(origin="x", lob="y").fixed_duration == 4976.5
