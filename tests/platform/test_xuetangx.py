"""学堂在线平台实现测试。"""

from typing import Any

import pytest

from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError, AuthError
from wkhelper.domain.homework import Homework
from wkhelper.infra.http import AsyncSession
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

        user = await platform.login_with_cookies({"csrftoken": "token123", "sessionid": "sess456"})
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
            await platform.login_with_cookies({"csrftoken": "only_token"})


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
async def test_xuetangx_get_courses_includes_completed(monkeypatch) -> None:
    """应同时拉取正在上课（1）与已结课（3）的课程。"""
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)
        requested: list[str] = []

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            status = kwargs["params"]["status"]
            requested.append(status)
            if status == "1":
                products = [
                    {"course_id": 97330, "name": "高等数学(2026)", "classroom_id": 1, "sign": "a"},
                    {"course_id": 97330, "name": "高等数学(2025)", "classroom_id": 2, "sign": "a"},
                ]
            else:
                products = [{"course_id": 1000, "name": "已结课课程", "classroom_id": 3, "sign": "b"}]
            return {"success": True, "data": {"product_list": products, "pages": 1}}

        monkeypatch.setattr(platform, "_request_json", fake_request_json)
        courses = await platform.get_courses()

    assert requested == ["1", "3"]
    # 同一 product_id 的不同学期课堂必须都保留
    assert [(course.id, course.metadata["classroom_id"]) for course in courses] == [
        ("97330", 1),
        ("97330", 2),
        ("1000", 3),
    ]


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

        chapters = await platform.get_chapter_data(course)
        assert len(chapters) == 1

        schedules = await platform.get_leaf_schedules(course)
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

        ctx = await platform.prepare_submit_context(hw, course)
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

        ctx = await platform.prepare_video_context("9001", course)
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

        unit = await platform.fetch_discussion_unit("leaf_456", course)
        assert unit["id"] == 101
        assert await platform.discussion_completed(unit, course, "leaf_456") is True
        assert await platform.discussion_completed({"user_comment_num": 0}, course, "leaf_456") is False

        resp = await platform.post_discussion_comment(unit, "hello", course, "leaf_456")
        assert resp["success"] is True


@pytest.mark.asyncio
async def test_web_task_url_matches_spa_route() -> None:
    """前端任务页 URL 必须符合 SPA 路由 /learn/{sign}/{course}/{classroom}/{kind}/{leaf}。"""
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)
        course = _sample_course()

        assert (
            platform.format_web_task_url(course, "75369926", "exercise")
            == "https://www.xuetangx.com/learn/mock-sign-123/1001/2001/exercise/75369926"
        )
        assert (
            platform.format_web_task_url(course, "75369927", "video")
            == "https://www.xuetangx.com/learn/mock-sign-123/1001/2001/video/75369927"
        )


@pytest.mark.asyncio
async def test_exam_leaf_rejected_for_exercise_api(monkeypatch) -> None:
    """考试节点不能走普通习题接口，必须给出明确指引而不是空题目。"""
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            return {"success": True, "data": {"leaf_type": 5, "content_info": {"leaf_type_id": 3908369}}}

        monkeypatch.setattr(platform, "_request_json", fake_request_json)

        with pytest.raises(APIError, match="是考试"):
            await platform.get_leaf_questions("75369926", _sample_course())


def test_parse_exam_problem_normalizes_fields() -> None:
    """考场试卷条目归一化：HTML 题干/选项转 Markdown，缺失字段有默认值。"""
    problem = XuetangXPlatform._parse_exam_problem(
        {
            "problem_id": 181540477,
            "TypeText": "单选题",
            "Body": "<p>1+1=? (  )</p>",
            "Options": [{"key": "A", "value": "<p>2</p>"}, {"key": "B", "value": "<p>3</p>"}],
            "score": 4,
            "Answer": ["A"],
            "HasRemark": False,
        },
        1,
    )

    assert problem.id == "181540477"
    assert problem.type == "单选题"
    assert problem.prompt == "1+1=? ( )"
    assert [(o.key, o.text) for o in problem.options] == [("A", "2"), ("B", "3")]
    assert problem.score == 4.0
    assert problem.answer == ["A"]

    empty = XuetangXPlatform._parse_exam_problem({}, 7)
    assert empty.index == 7
    assert empty.id == "7"
    assert empty.answer == []


def test_parse_exam_problem_extracts_images() -> None:
    """题干中的插图抽到 images，正文只保留 `[图]` 占位，避免 Markdown 表格被 URL 污染。"""
    problem = XuetangXPlatform._parse_exam_problem(
        {
            "problem_id": 181540477,
            "TypeText": "单选题",
            "Body": "<p><img src='http://sc0.ykt.io/ue_i/a.png'> (  )</p>",
            "Options": [],
            "score": 4,
        },
        1,
    )

    assert problem.prompt == "[图] ( )"
    assert problem.images == ["http://sc0.ykt.io/ue_i/a.png"]
    assert problem.answer == []


@pytest.mark.asyncio
async def test_exam_cover_limit_is_seconds(monkeypatch) -> None:
    """封面 limit 以秒为单位（前端 1e3*limit 换算毫秒），0 表示不限时。"""
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "exam/cover" in url:
                return {"data": {"data": {"title": "限时卷", "problem_count": 10, "total_score": 50.0, "limit": 5400}}}
            return {"data": {"leaf_type": 5, "content_info": {"leaf_type_id": 42}}}

        monkeypatch.setattr(platform, "_request_json", fake_request_json)
        cover = await platform.get_exam_cover("999", _sample_course())
        assert cover.limit_minutes == 90
        assert cover.has_limit is True

        async def no_limit(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "exam/cover" in url:
                return {"data": {"data": {"title": "不限时卷", "limit": 0}}}
            return {"data": {"leaf_type": 5, "content_info": {"leaf_type_id": 43}}}

        monkeypatch.setattr(platform, "_request_json", no_limit)
        cover = await platform.get_exam_cover("999", _sample_course())
        assert cover.limit_minutes == 0
        assert cover.has_limit is False


@pytest.mark.asyncio
async def test_exam_cover_proctoring_flags_are_distinct(monkeypatch) -> None:
    """Web 截屏与 App 截屏必须分开建模，不能用 `or` 合并成同一个布尔。"""
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "exam/cover" in url:
                return {
                    "data": {
                        "data": {
                            "title": "监考卷",
                            "capture_screen": 0,
                            "app_capture_screen": 1,
                            "app_front_random_snap": 1,
                            "open_screen_cuts": 1,
                            "max_screen_cuts_num": 3,
                            "online_proctor": 0,
                            "identity_auth": 0,
                            "max_retry": -1,
                            "is_offline": False,
                            "face_auth_status": {"online_proctor": 1, "has_idphoto": True},
                        }
                    }
                }
            return {"data": {"leaf_type": 5, "content_info": {"leaf_type_id": 44}}}

        monkeypatch.setattr(platform, "_request_json", fake_request_json)
        cover = await platform.get_exam_cover("999", _sample_course())

        assert cover.capture_screen is False
        assert cover.app_capture_screen is True
        assert cover.front_random_snap is True
        assert cover.screen_cuts is True
        assert cover.max_screen_cuts == 3
        assert cover.online_proctor is True
        assert cover.identity_auth is True
        assert cover.face_auth is True
        assert cover.max_retry == -1
        assert cover.is_offline is False


@pytest.mark.asyncio
async def test_bound_exam_room_skips_token_mint(monkeypatch) -> None:
    """绑定已有考场会话后不得再次换票，否则会把浏览器端挤下线。"""
    async with AsyncSession() as client:
        platform = XuetangXPlatform(client)
        platform.bind_exam_room("https://examination.test", 42, "https://examination.test/exam/42")

        async def boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("不应重新换票")

        monkeypatch.setattr(platform, "_mint_exam_room", boom)
        assert await platform._open_exam_room("999", _sample_course()) == (
            "https://examination.test",
            42,
            "https://examination.test/exam/42",
        )
