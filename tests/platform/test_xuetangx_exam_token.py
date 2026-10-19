"""学堂在线考场换票与考试节点校验测试。"""

from typing import Any

import pytest

from wkhelper.core.entities import Course, UserInfo
from wkhelper.core.exceptions import APIError
from wkhelper.core.transport import AsyncSession
from wkhelper.platform.xuetangx import XuetangXPlatform


def _course() -> Course:
    return Course(
        id="1001",
        name="计算机体系结构",
        platform_id="xtzx",
        metadata={"classroom_id": 2001, "sign": "mock-sign-123"},
    )


class _Resp:
    status_code = 200


def _platform(client: AsyncSession) -> XuetangXPlatform:
    platform = XuetangXPlatform(client)
    platform.user = UserInfo(id="42", name="tester")
    return platform


@pytest.mark.asyncio
async def test_exam_cover_rejects_homework_leaf(monkeypatch: pytest.MonkeyPatch) -> None:
    """非考试节点不得返回全零封面，必须指引到作业命令。"""
    async with AsyncSession() as client:
        platform = _platform(client)

        async def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            return {"success": True, "data": {"leaf_type": 6, "content_info": {"leaf_type_id": 9001}}}

        monkeypatch.setattr(platform, "_request_json", fake_request)

        with pytest.raises(APIError, match="不是考试"):
            await platform.get_exam_cover("1", _course())


@pytest.mark.asyncio
async def test_mint_exam_room_rejects_empty_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """令牌为空时不得继续拼登录地址并报告成功。"""
    async with AsyncSession() as client:
        platform = _platform(client)

        async def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "leaf_info" in url:
                return {"success": True, "data": {"leaf_type": 5, "content_info": {"leaf_type_id": 777}}}
            return {"success": True, "data": {"data": {"crypt": "", "exam_host": "https://exam.test"}}}

        monkeypatch.setattr(platform, "_request_json", fake_request)

        with pytest.raises(APIError, match="未返回考场令牌"):
            await platform._mint_exam_room("1", _course())


@pytest.mark.asyncio
async def test_mint_exam_room_encodes_login_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """登录地址需按前端契约编码 crypt 与 next。"""
    async with AsyncSession() as client:
        platform = _platform(client)

        async def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "leaf_info" in url:
                return {"success": True, "data": {"leaf_type": 5, "content_info": {"leaf_type_id": 777}}}
            return {
                "success": True,
                "data": {"data": {"crypt": "tok+1", "exam_host": "https://exam.test/"}},
            }

        async def fake_get(url: str, **kwargs: Any) -> _Resp:
            return _Resp()

        monkeypatch.setattr(platform, "_request_json", fake_request)
        monkeypatch.setattr(platform.client, "get", fake_get)

        host, eid, login_url = await platform._mint_exam_room("1", _course())

    assert host == "https://exam.test"
    assert eid == 777
    assert "crypt=tok%2B1" in login_url
    assert "next=https%3A%2F%2Fexam.test%2Fexam%2F777%3FisFrom%3D0" in login_url
