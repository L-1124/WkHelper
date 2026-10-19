"""学堂在线平台实现测试。"""

from typing import Any

import pytest

from wkhelper.core.entities import Course, Homework
from wkhelper.core.exceptions import APIError, AuthError
from wkhelper.core.transport import AsyncSession
from wkhelper.platform.xuetangx import XuetangXPlatform


def _sample_course() -> Course:
    return Course(
        id="1001",
        name="计算机体系结构",
        platform_id="xtzx",
        metadata={"classroom_id": 2001, "sign": "mock-sign-123"},
    )


@pytest.mark.asyncio
async def test_xuetangx_login_with_cookies_success(monkeypatch) -> None:
    """验证 Cookie 包含有效 csrftoken 与 sessionid 时正确解析用户资料及指纹头。"""
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            assert "basic_profile" in url
            return {
                "success": True,
                "data": {"id": 12345, "name": "学堂用户", "school": "清华大学"},
            }

        monkeypatch.setattr(platform, "_request_json", fake_request_json)

        user = await platform._login_with_cookies({"csrftoken": "token123", "sessionid": "sess456"})
        assert user.id == 12345
        assert user.name == "学堂用户"
        assert user.school == "清华大学"
        assert client.headers.get("X-CSRFToken") == "token123"
        assert client.headers.get("app-name") == "xtzx"
        assert client.headers.get("terminal-type") == "web"
        assert client.headers.get("django-language") == "zh"
        assert client.headers.get("Accept-Language") == "zh"
        assert client.headers.get("xtbz") == "xt"
        assert client.headers.get("x-client") == "web"
        assert platform.heartbeat_profile.lob == "xt"


@pytest.mark.asyncio
async def test_xuetangx_login_with_cookies_missing_token() -> None:
    """Cookie 缺少 csrftoken 或 sessionid 时应拒绝登录。"""
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)
        with pytest.raises(AuthError, match="缺少 csrftoken 或 sessionid"):
            await platform._login_with_cookies({"csrftoken": "only_token"})


@pytest.mark.asyncio
async def test_xuetangx_get_courses(monkeypatch) -> None:
    """验证获取用户课程列表并映射课堂 ID 与签名元数据。"""
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            assert "user-courses" in url
            return {
                "success": True,
                "data": {
                    "product_list": [
                        {
                            "course_id": 999,
                            "name": "大学物理",
                            "classroom_id": 888,
                            "sign": "sign-xyz",
                        }
                    ]
                },
            }

        monkeypatch.setattr(platform, "_request_json", fake_request_json)

        courses = await platform.get_courses()
        assert len(courses) == 1
        assert courses[0].id == "999"
        assert courses[0].name == "大学物理"
        assert courses[0].platform_id == "xtzx"
        assert courses[0].metadata["classroom_id"] == 888
        assert courses[0].metadata["sign"] == "sign-xyz"


@pytest.mark.asyncio
async def test_xuetangx_get_courses_failure(monkeypatch) -> None:
    """接口返回失败时应抛出 APIError。"""
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            return {"success": False}

        monkeypatch.setattr(platform, "_request_json", fake_request_json)

        with pytest.raises(APIError, match="获取课程列表失败"):
            await platform.get_courses()


@pytest.mark.asyncio
async def test_xuetangx_get_chapter_and_schedule(monkeypatch) -> None:
    """验证章节大纲查询与进度字典解析。"""
    course = _sample_course()
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "course/chapter" in url:
                assert kwargs.get("params") == {"cid": "2001", "sign": "mock-sign-123"}
                return {"data": {"course_chapter": [{"id": 1, "name": "第一章"}]}}
            if "course/schedule" in url:
                assert kwargs.get("params") == {"cid": "2001", "sign": "mock-sign-123"}
                return {
                    "success": True,
                    "data": {
                        "leaf_schedules": {
                            "3001": 1.0,
                            "3002": "0.5",
                            "bad": "invalid",
                        }
                    },
                }
            return {}

        monkeypatch.setattr(platform, "_request_json", fake_request_json)

        chapters = await platform._get_chapter_data(course)
        assert len(chapters) == 1

        schedules = await platform._get_leaf_schedules(course)
        assert schedules == {3001: 1.0, 3002: 0.5}


@pytest.mark.asyncio
async def test_xuetangx_submit_payload_and_context(monkeypatch) -> None:
    """验证作业提交上下文准备与请求载荷构建。"""
    course = _sample_course()
    hw = Homework(id="5001", name="测试作业")

    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            assert "leaf_info/2001/5001" in url
            assert kwargs.get("params", {}).get("sign") == "mock-sign-123"
            return {
                "data": {
                    "content_info": {
                        "leaf_type_id": 7777,
                    }
                }
            }

        monkeypatch.setattr(platform, "_request_json", fake_request_json)

        ctx = await platform._prepare_submit_context(hw, course)
        assert ctx["leaf_id"] == "5001"
        assert ctx["exercise_id"] == 7777
        assert ctx["sign"] == "mock-sign-123"
        assert ctx["classroom_id"] == 2001

        payload = platform._build_submit_payload(1234, ["A"], submit_ctx=ctx)
        assert payload["classroom_id"] == 2001
        assert payload["problem_id"] == 1234
        assert payload["answer"] == ["A"]
        assert payload["answers"] == {}
        assert payload["exercise_id"] == 7777
        assert payload["leaf_id"] == "5001"
        assert payload["sign"] == "mock-sign-123"

        # 填空题验证
        fill_payload = platform._build_submit_payload(1234, ["ans1", "ans2"], submit_ctx=ctx, question={"type": 4})
        assert fill_payload["answer"] == ""
        assert fill_payload["answers"] == {"1": "ans1", "2": "ans2"}

        assert platform._build_submit_url() == "https://www.xuetangx.com/api/v1/lms/exercise/problem_apply/"
        assert (
            platform._build_exercise_list_url(7777, sku_id=222)
            == "https://www.xuetangx.com/api/v1/lms/exercise/get_exercise_list/7777/222/"
        )
        assert platform._build_exercise_list_url(7777) == "https://www.xuetangx.com/api/v1/lms/exercise/get_exercise_list/7777/"


@pytest.mark.asyncio
async def test_xuetangx_video_context(monkeypatch) -> None:
    """验证视频上下文与心跳地址构造。"""
    course = _sample_course()
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            assert "leaf_info/2001/9001" in url
            return {
                "data": {
                    "user_id": 111,
                    "sku_id": 222,
                    "course_id": 333,
                    "classroom_id": "2001",
                    "university_id": "999",
                    "content_info": {
                        "media": {
                            "ccid": "ccid_123",
                            "duration": 120.5,
                        }
                    },
                }
            }

        monkeypatch.setattr(platform, "_request_json", fake_request_json)

        ctx = await platform._prepare_video_context("9001", course)
        assert ctx.video_id == "9001"
        assert ctx.user_id == 111
        assert ctx.sku_id == 222
        assert ctx.course_id == 333
        assert ctx.classroom_id == "2001"
        assert ctx.progress_url == "https://www.xuetangx.com/video-log/get_video_watch_progress/"
        assert ctx.heartbeat_url == "https://www.xuetangx.com/video-log/heartbeat/"
        assert ctx.progress_params["cid"] == 333
        assert ctx.media_ccid == "ccid_123"
        assert ctx.duration == 120.5
        assert ctx.request_kwargs == {
            "headers": {
                "uv-id": "999",
                "university-id": "999",
                "classroom-id": "2001",
            }
        }


@pytest.mark.asyncio
async def test_xuetangx_article_and_discussions(monkeypatch) -> None:
    """验证学堂在线图文参数与讨论题接口 hook。"""
    course = _sample_course()
    course.metadata["sku_id"] = "sku_888"
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)

        assert platform._article_finish_params(course) == {"cid": "2001", "sid": "sku_888"}

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "forum/unit/discussion" in url:
                assert kwargs.get("params", {}).get("channel") == "xt"
                return {"data": {"id": 101, "user_id": 555, "user_comment_num": 1}}
            if "forum/comment" in url:
                assert method == "POST"
                assert kwargs.get("params", {}).get("classroom_id") == "2001"
                assert kwargs.get("json", {}).get("topic_id") == 101
                assert kwargs.get("json", {}).get("content", {}).get("text") == "hello"
                return {"success": True, "data": {"id": 202}}
            raise NotImplementedError(url)

        monkeypatch.setattr(platform, "_request_json", fake_request_json)

        unit = await platform._fetch_discussion_unit("leaf_456", course)
        assert unit["id"] == 101
        assert await platform._discussion_completed(unit, course, "leaf_456") is True
        assert await platform._discussion_completed({"user_comment_num": 0}, course, "leaf_456") is False

        resp = await platform._post_discussion_comment(unit, "hello", course, "leaf_456")
        assert resp["success"] is True
