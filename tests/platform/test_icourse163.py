"""中国大学MOOC (icourse163) 平台实现测试。"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from wkhelper.domain.courses import Course
from wkhelper.domain.errors import AuthError
from wkhelper.infra.http import AsyncSession
from wkhelper.platform.icourse163 import Icourse163Platform
from wkhelper.platform.parsing import iter_leaves_with_context


def _sample_course() -> Course:
    return Course(
        id="1461677455",
        name="计算机导论",
        platform_id="icourse163",
        metadata={"term_id": "1461677455", "course_id": "1001001"},
    )


@pytest.mark.asyncio
async def test_icourse163_login_with_cookies_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """首页 JSON 中的认证用户用于校验 Cookie。"""
    async with AsyncSession() as client:
        platform = Icourse163Platform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "webUser": {
                    "id": 888888,
                    "realName": "张三",
                    "nickName": "zhangsan_mooc",
                    "schoolName": "浙江大学",
                },
            }

        monkeypatch.setattr(platform, "_request_json", fake_request_json)

        user = await platform.login_with_cookies({"NTESSTUDYSI": "sample_csrf", "STUDY_SESS": "sess123"})
        assert user.id == "888888"
        assert user.name == "张三"
        assert user.school == "浙江大学"


@pytest.mark.asyncio
async def test_icourse163_login_not_auth_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """未登录或失效时抛出脱敏 AuthError。"""
    async with AsyncSession() as client:
        platform = Icourse163Platform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            return {"webUser": None}

        monkeypatch.setattr(platform, "_request_json", fake_request_json)

        with pytest.raises(AuthError, match="登录凭据无效或已过期"):
            await platform.login_with_cookies({"NTESSTUDYSI": "expired_token"})


@pytest.mark.asyncio
async def test_icourse163_get_courses_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实面板结构按学期去重，并遍历平台分页。"""
    async with AsyncSession() as client:
        platform = Icourse163Platform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "getMyLearnedCoursePanelList.rpc" not in url:
                return {"code": -2, "message": "member 不能为空"}
            page = kwargs["data"]["p"]
            items: list[dict[str, Any]] = [
                {
                    "id": 1001001,
                    "name": "计算机导论",
                    "termPanel": {"id": 1461677455},
                    "schoolPanel": {"name": "浙江大学"},
                }
            ]
            if page == 2:
                items.append({"id": 1001002, "name": "操作系统", "termPanel": {"id": 1461677456}, "schoolPanel": None})
            return {"code": 0, "result": {"pagination": {"totlePageCount": 2}, "result": items}}

        monkeypatch.setattr(platform, "_request_json", fake_request_json)
        courses = await platform.get_courses()
        assert [(c.id, c.name) for c in courses] == [("1461677455", "计算机导论"), ("1461677456", "操作系统")]
        assert courses[0].metadata["course_id"] == "1001001"
        assert courses[0].metadata["school_name"] == "浙江大学"
        assert courses[1].metadata["school_name"] is None


@pytest.mark.asyncio
async def test_icourse163_get_chapter_data_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """单元类型与章级评测保持独立身份、分类及截止时间。"""
    async with AsyncSession() as client:
        platform = Icourse163Platform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "code": 0,
                "result": {
                    "chapters": [
                        {
                            "id": 101,
                            "name": "第一章",
                            "lessons": [
                                {
                                    "id": 201,
                                    "name": "概论",
                                    "units": [
                                        {"id": 301, "name": "视频", "contentType": 1, "durationInSeconds": 300},
                                        {"id": 302, "name": "讲义", "contentType": 3},
                                        {"id": 303, "name": "拓展阅读", "contentType": 4},
                                        {"id": 304, "name": "随堂测验", "contentType": 5},
                                        {"id": 305, "name": "章节讨论", "contentType": 6},
                                        {"id": 306, "name": "隐藏内容", "contentType": 4, "visible": 0},
                                        {"id": 307, "name": "未知资源", "contentType": 99},
                                    ],
                                }
                            ],
                            "homeworks": [{"id": 401, "name": "章作业", "test": {"id": 501, "deadline": 1800000000000}}],
                            "quizs": [{"id": 402, "name": "章测验", "test": {"id": 502, "deadline": 1800000000001}}],
                            "exam": {"id": 503, "name": "期末考试", "releaseTime": 1790000000000, "deadline": 1800000000002},
                        }
                    ]
                },
            }

        monkeypatch.setattr(platform, "_request_json", fake_request_json)
        chapters = await platform.get_chapter_data(_sample_course())
        leaves = {leaf["id"]: leaf for leaf, _, _ in iter_leaves_with_context(chapters)}
        assert {lid: leaf["leaf_type"] for lid, leaf in leaves.items()} == {
            301: 0,
            302: 7,
            303: 3,
            304: 9,
            305: 4,
            306: 3,
            307: -1,
            501: 6,
            502: 6,
            503: 5,
        }
        assert leaves[301]["duration"] == 300
        assert leaves[306]["is_show"] is False
        assert leaves[501]["score_deadline"] == 1800000000000
        assert leaves[502]["score_deadline"] == 1800000000001
        assert leaves[503]["start_time"] == 1790000000000


@pytest.mark.asyncio
async def test_icourse163_get_leaf_schedules(monkeypatch: pytest.MonkeyPatch) -> None:
    """解包真实进度响应，保留小数并区分部分完成与已完成。"""
    async with AsyncSession() as client:
        platform = Icourse163Platform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "code": 0,
                "result": {
                    "lastLearnUnitId": 302,
                    "mocTermDto": {
                        "chapters": [
                            {
                                "lessons": [
                                    {
                                        "units": [
                                            {"id": 301, "completePercent": 1.0},
                                            {"id": 302, "completePercent": 0.99},
                                            {"id": 303, "completePercent": 0.25},
                                            {"id": 304, "completePercent": 0.0},
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                },
            }

        monkeypatch.setattr(platform, "_request_json", fake_request_json)
        schedules = await platform.get_leaf_schedules(_sample_course())
        assert schedules == {301: 1.0, 302: 0.99, 303: 0.25, 304: 0.0}
        assert {lid for lid, value in schedules.items() if value >= 1.0} == {301}


@pytest.mark.asyncio
async def test_icourse163_outline_falls_back_to_wrapped_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """公开大纲为空时，已学习大纲仍可恢复任务树。"""
    async with AsyncSession() as client:
        platform = Icourse163Platform(client)

        async def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if "getMocTermDto.rpc" in url:
                return {"code": 0, "result": None}
            return {
                "code": 0,
                "result": {"mocTermDto": {"chapters": [{"id": 1, "lessons": [{"id": 2, "units": [{"id": 3, "contentType": 1}]}]}]}},
            }

        monkeypatch.setattr(platform, "_request_json", fake_request_json)
        chapters = await platform.get_chapter_data(_sample_course())
        assert [leaf["id"] for leaf, _, _ in iter_leaves_with_context(chapters)] == [3]


@pytest.fixture
def mooc_clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """只替换业务循环的时钟，避免污染 pytest 事件循环。"""
    from wkhelper.video import icourse163

    clock = SimpleNamespace(now=0.0)

    async def sleep(delay: float) -> None:
        clock.now += delay

    @asynccontextmanager
    async def timeout_at(deadline: float):
        yield

    monkeypatch.setattr(
        icourse163,
        "asyncio",
        SimpleNamespace(get_running_loop=lambda: SimpleNamespace(time=lambda: clock.now), sleep=sleep, timeout_at=timeout_at),
    )
    return clock


@pytest.fixture
def mooc_server(monkeypatch: pytest.MonkeyPatch, mooc_clock: SimpleNamespace):
    """课程记录与 VOD 分路，只有持久化的学习写才能改变断点。"""
    state = SimpleNamespace(
        duration=469,
        start=359,
        rate=0.99,
        view=0,
        persist=True,
        freeze_rate=False,
        fail_learning=False,
        fail_vod=False,
        calls=[],
        vod=[],
        events=[],
        resume_field="start",
        resume_value=359,
    )

    def install(platform: Icourse163Platform) -> None:
        platform.current_cookies = {"NTESSTUDYSI": "local-csrf"}
        from wkhelper.domain.accounts import UserInfo

        platform.user = UserInfo(id="888", name="本地用户")

        async def read(method: str, url: str, **kwargs: Any):
            unit = {
                "id": 301,
                "contentId": 9001,
                "contentType": 1,
                "viewStatus": state.view,
                "durationInSeconds": state.duration,
                "completePercent": state.rate,
            }
            tree = {"chapters": [{"id": 101, "lessons": [{"id": 201, "viewStatus": 5, "units": [unit]}]}]}
            if "getLessonUnitLearnVo" in url:
                video = {"videoId": 9001, "duration": state.duration}
                result = {"videoVo": video}
                if state.resume_field == "start":
                    video["start"] = state.resume_value
                elif state.resume_field == "videoLearnTime":
                    result["videoLearnTime"] = state.resume_value
                return {"code": 0, "result": result}
            return {"code": 0, "result": {"mocTermDto": tree} if "getLastLearned" in url else tree}

        async def write(method: str, url: str, **kwargs: Any):
            assert url.endswith("/web/j/courseRpcBean.saveMocContentLearn.rpc")
            dto = json.loads(kwargs["data"])["dto"]
            state.calls.append((mooc_clock.now, dto))
            if dto["videoTime"] == -1:
                state.view = 5
            else:
                if state.fail_learning:
                    return {"code": -1, "result": False}
                if state.persist:
                    state.resume_value = dto["videoTime"]
                    if not state.freeze_rate:
                        state.rate = dto["videoTime"] / state.duration
            return {"code": 0, "result": True}

        async def vod(url: str, **kwargs: Any):
            assert url == platform.vod_heartbeat_url
            assert "Auth-Signature" not in kwargs["headers"]
            state.vod.append((mooc_clock.now, json.loads(kwargs["data"])))

            def check() -> None:
                if state.fail_vod:
                    raise RuntimeError("local HTTP 500")

            return SimpleNamespace(raise_for_status=check)

        monkeypatch.setattr(platform, "_request_json", read)
        monkeypatch.setattr(platform, "_request_json_once", write)
        monkeypatch.setattr(platform.client, "post", vod)

    state.install = install
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [0.99, 1.0])
async def test_mooc_view_resume_and_dual_rhythm(mooc_server, rate: float) -> None:
    """已查看和高比例不能跳过官方断点，两个写通道独立计时。"""
    from wkhelper.video.engine import VideoWatchOptions
    from wkhelper.video.service import do_video

    state = mooc_server
    state.rate = rate
    async with AsyncSession() as client:
        platform = Icourse163Platform(client)
        state.install(platform)
        result = await do_video(
            platform,
            "301",
            "视频",
            _sample_course(),
            VideoWatchOptions(heartbeat_interval=10, jitter=0, video_timeout=61),
            lambda event, payload: state.events.append((event, payload)),
        )
    visit, learning = [dto for _, dto in state.calls]
    assert visit["videoTime"] == -1 and visit["learnedVideoTimeCount"] == 0 and visit["finished"] is False
    assert "index" not in visit
    assert learning["index"] == 1 and learning["videoTime"] == 419
    assert learning["learnedVideoTimeCount"] == 60 and learning["finished"] is False
    assert [time for time, _ in state.vod] == [10, 20, 30, 40, 50, 60]
    assert all(body["videoId"] == 9001 for _, body in state.vod)
    assert result.status == "timed_out" and not result.platform_completed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("persist", "fail_learning", "fail_vod", "freeze_rate", "expected"),
    [
        (True, False, False, False, "completed"),
        (False, False, False, False, "timed_out"),
        (True, True, False, False, "failed"),
        (True, False, True, False, "completed"),
        (True, False, False, True, "timed_out"),
    ],
)
async def test_mooc_acceptance_requires_both_persisted_values(
    mooc_server,
    persist: bool,
    fail_learning: bool,
    fail_vod: bool,
    freeze_rate: bool,
    expected: str,
) -> None:
    """末尾只写一次，VOD 失败可继续，课程失败不重放。"""
    from wkhelper.video.engine import VideoWatchOptions
    from wkhelper.video.service import do_video

    state = mooc_server
    state.duration, state.resume_value, state.rate = 21, 1, 1.0
    state.persist, state.fail_learning, state.fail_vod = persist, fail_learning, fail_vod
    state.freeze_rate = freeze_rate
    if freeze_rate:
        state.rate = 0.0
    async with AsyncSession() as client:
        platform = Icourse163Platform(client)
        state.install(platform)
        result = await do_video(
            platform,
            "301",
            "视频",
            _sample_course(),
            VideoWatchOptions(heartbeat_interval=10, jitter=0, video_timeout=25, progress_interval=2),
            lambda event, payload: state.events.append((event, payload)),
        )
    assert result.status == expected
    assert result.platform_completed is (expected == "completed")
    assert [time for time, _ in state.vod] == [10]
    assert [dto["finished"] for _, dto in state.calls] == [False, True]
    assert state.calls[-1][1]["videoTime"] == 21
    assert state.calls[-1][1]["learnedVideoTimeCount"] == 20
    if fail_vod:
        assert any(event == "video.status" and "VOD" in payload["message"] for event, payload in state.events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("start", 0, 0),
        ("videoLearnTime", 12, 12),
        ("missing", None, 0),
        ("start", -1, None),
        ("start", float("inf"), None),
        ("start", 470, None),
    ],
)
async def test_mooc_official_resume_boundary(mooc_server, field: str, value, expected) -> None:
    """合法零与缺失分别处理，损坏断点不能变成从头或比例推算。"""
    from wkhelper.domain.errors import APIError

    state = mooc_server
    state.resume_field, state.resume_value = field, value
    async with AsyncSession() as client:
        platform = Icourse163Platform(client)
        state.install(platform)
        if expected is None:
            with pytest.raises(APIError):
                await platform.prepare_video_context("301", _sample_course())
        else:
            context = await platform.prepare_video_context("301", _sample_course())
            assert context.start == expected


@pytest.mark.asyncio
async def test_mooc_plan_preserves_auth_failure(mooc_server, monkeypatch: pytest.MonkeyPatch) -> None:
    """学习写认证失效保留退出码语义，即使计划允许继续也不重放。"""
    from wkhelper.video.plan import VideoTarget
    from wkhelper.video.runner import VideoPlanRunner

    calls = []
    async with AsyncSession() as client:
        platform = Icourse163Platform(client)
        mooc_server.install(platform)

        async def reject(method: str, url: str, **kwargs: Any):
            calls.append(json.loads(kwargs["data"])["dto"])
            return {"code": 1, "message": "not_auth"}

        monkeypatch.setattr(platform, "_request_json_once", reject)
        course = _sample_course()
        with pytest.raises(AuthError):
            await VideoPlanRunner(platform).execute_video_plan(
                {str(course.id): course}, [VideoTarget(course_id=str(course.id), video_id="301")], on_error="continue"
            )
    assert len(calls) == 1 and calls[0]["videoTime"] == -1
