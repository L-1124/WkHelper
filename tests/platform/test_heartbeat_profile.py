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
        video_frame=120.0,
        seq=4,
        timestamp=1700000000000,
        cc="AD3012E4392D238C9C33DC5901307461",
        duration=249.3,
    )


@pytest.mark.asyncio
async def test_heartbeat_payload_uses_platform_profile() -> None:
    """心跳报文的平台固定字段来自 `heartbeat_profile`，字段语义对齐 Web 播放器。"""
    async with niquests.AsyncSession() as client:
        for platform in (YuketangPlatform(client), XuetangXPlatform(client)):
            profile = platform.heartbeat_profile
            payload = _build(platform)

            # 平台差异字段
            assert payload["lob"] == profile.lob
            assert payload["n"] == profile.n
            # 通用契约字段
            assert payload["i"] == 5  # 采样间隔（秒）
            assert payload["et"] == "heartbeat"
            assert payload["p"] == "web"
            assert payload["sp"] == 1.0
            assert payload["sq"] == 4  # 单调自增序号，不是循环下标
            assert payload["ts"] == "1700000000000"  # 毫秒时间戳字符串
            assert payload["cc"] == "AD3012E4392D238C9C33DC5901307461"
            assert payload["d"] == 249.3
            assert payload["v"] == 1001
            assert payload["skuid"] == 9
            assert payload["classroomid"] == "2001"
            assert payload["cards_id"] == 0 and payload["slide"] == 0 and payload["v_url"] == ""
            assert str(payload["pg"]).startswith("1001_")


def test_heartbeat_lob_differs_per_platform() -> None:
    """学堂在线 `lob=xt`、雨课堂 `lob=ykt`，不可混用。"""
    assert XuetangXPlatform(None).heartbeat_profile.lob == "xt"  # type: ignore[arg-type]
    assert YuketangPlatform(None).heartbeat_profile.lob == "ykt"  # type: ignore[arg-type]


def test_heartbeat_profile_is_required() -> None:
    """未声明心跳档案的平台无法实例化，避免再次出现静默继承。"""

    class IncompletePlatform(BasePlatform):
        async def _login_with_cookies(self, cookies):  # noqa: ANN001, ANN202
            raise NotImplementedError

        async def _qr_exchange_cookies(self, login_data):  # noqa: ANN001, ANN202
            return {}

        async def get_courses(self):
            return []

        async def _get_chapter_data(self, course):  # noqa: ANN001, ANN202
            return []

        async def _get_leaf_schedules(self, course):  # noqa: ANN001, ANN202
            return {}

        async def _prepare_video_context(self, video_id, course):  # noqa: ANN001, ANN202
            raise NotImplementedError

        async def _prepare_submit_context(self, homework, course):  # noqa: ANN001, ANN202
            return {}

    with pytest.raises(TypeError):
        IncompletePlatform(None)  # type: ignore[arg-type]

    assert HeartbeatProfile(lob="x").interval == 5
