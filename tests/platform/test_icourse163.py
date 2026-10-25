"""中国大学MOOC (icourse163) 平台实现测试。"""

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
