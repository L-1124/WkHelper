"""雨课堂考场换票与 fail-closed 行为测试。"""

from typing import Any

import pytest

from wkhelper.domain.accounts import UserInfo
from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError
from wkhelper.infra.http import AsyncSession
from wkhelper.platform.yuketang import YuketangPlatform


def _course() -> Course:
    return Course(id="1", name="测试课程", platform_id="ykt", metadata={"classroom_id": 2001, "university_id": 9})


class _Resp:
    status_code = 200


def _platform(client: AsyncSession) -> YuketangPlatform:
    platform = YuketangPlatform(client)
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
            return {"success": True, "data": {"token": "", "user_id": 42, "exam_host": "https://exam.test"}}

        monkeypatch.setattr(platform, "_request_json", fake_request)

        with pytest.raises(APIError, match="未返回考场令牌"):
            await platform._mint_exam_room("1", _course())


@pytest.mark.asyncio
async def test_mint_exam_room_rejects_business_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """平台 gen_token 返回业务错误时必须原样上报原因。"""
    async with AsyncSession() as client:
        platform = _platform(client)

        async def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "leaf_info" in url:
                return {"success": True, "data": {"leaf_type": 5, "content_info": {"leaf_type_id": 888}}}
            return {"success": False, "errcode": 500, "errmsg": "非考试节点"}

        monkeypatch.setattr(platform, "_request_json", fake_request)

        with pytest.raises(APIError, match="非考试节点"):
            await platform._mint_exam_room("2", _course())


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
                "data": {"token": "tok+1", "user_id": 42, "exam_host": "https://exam.test/"},
            }

        async def fake_get(url: str, **kwargs: Any) -> _Resp:
            return _Resp()

        monkeypatch.setattr(platform, "_request_json", fake_request)
        monkeypatch.setattr(platform.client, "get", fake_get)

        host, eid, login_url = await platform._mint_exam_room("1", _course())

    assert host == "https://exam.test"
    assert eid == 777
    assert "crypt=tok%2B1" in login_url
    assert "next=https%3A%2F%2Fexam.test%2Fexam%2F777%3FisFrom%3D2" in login_url
    assert login_url.endswith("&language=zh")


@pytest.mark.asyncio
async def test_exam_cover_limit_is_seconds_and_status_six_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """考场 limit 为秒，需换算分钟；status=6 表示缺考。"""
    async with AsyncSession() as client:
        platform = _platform(client)

        async def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "leaf_info" in url:
                return {"data": {"leaf_type": 5, "content_info": {"leaf_type_id": 777}}}
            return {"data": {"data": {"title": "期末", "limit": 5400, "result": {"status": 6}}}}

        monkeypatch.setattr(platform, "_request_json", fake_request)
        cover = await platform.get_exam_cover("1", _course())

    assert cover.limit_minutes == 90
    assert cover.status == "已缺考"


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "target"), [(0, "start"), (1, "exam"), (6, "result")])
async def test_mint_next_target_follows_status(monkeypatch: pytest.MonkeyPatch, status: int, target: str) -> None:
    """换票目标路径需按考场状态切换。"""
    from urllib.parse import unquote

    async with AsyncSession() as client:
        platform = _platform(client)

        async def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "leaf_info" in url:
                return {"success": True, "data": {"leaf_type": 5, "content_info": {"leaf_type_id": 777}}}
            return {"success": True, "data": {"token": "tok", "user_id": 42, "exam_host": "https://exam.test"}}

        async def fake_get(url: str, **kwargs: Any) -> _Resp:
            return _Resp()

        monkeypatch.setattr(platform, "_request_json", fake_request)
        monkeypatch.setattr(platform.client, "get", fake_get)

        _host, _eid, login_url = await platform._mint_exam_room("1", _course(), status)

    assert f"https://exam.test/{target}/777?isFrom=2" in unquote(login_url)


@pytest.mark.asyncio
async def test_start_exam_reuses_bound_room(monkeypatch: pytest.MonkeyPatch) -> None:
    """已绑定考场会话时不得重新换票，避免把浏览器端挤下线。"""
    async with AsyncSession() as client:
        platform = _platform(client)
        platform.bind_exam_room("https://exam.test", 42, "https://exam.test/exam/42")
        calls: list[str] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            calls.append(url)
            if "leaf_info" in url:
                return {"data": {"leaf_type": 5, "content_info": {"leaf_type_id": 777}}}
            return {"data": {"data": {"title": "期末", "limit": 0, "result": {"status": 1}}}}

        monkeypatch.setattr(platform, "_request_json", fake_request)
        result = await platform.start_exam("1", _course())

    assert result.web_url == "https://exam.test/exam/42"
    assert not any("gen_token" in url for url in calls)
