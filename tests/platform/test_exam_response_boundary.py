"""考场响应边界模型容错与额外字段隔离测试。"""

from typing import Any

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.domain.courses import Course
from wkhelper.domain.exam import ExamCoverDetail, ExamProblemItem
from wkhelper.infra.http import AsyncSession
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.protocol_types import (
    PlatformExamCacheData,
    PlatformExamCoverData,
    PlatformExamOptionRaw,
    PlatformExamProblemRaw,
    PlatformExamResult,
    PlatformExamTokenData,
    PlatformNoticeData,
)


class _DummyPlatform(FakeAsyncPlatform):
    pass


def _dummy_course() -> Course:
    return Course(id="101", name="测试课程", platform_id="xtzx")


@pytest.mark.asyncio
async def test_exam_cache_boundary_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    """考场暂存作答容错：非字典跳过、空答案与空 problem_id 剔除、非列表与空数据安全兜底。"""
    async with AsyncSession() as client:
        plat = _DummyPlatform(client)
        plat.bind_exam_room("https://exam.test", 1, "https://login", leaf_id="1")

        # 1. 包含 None, int, 缺失 problem_id, 空字符串等混合列表
        async def fake_resp_mixed(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "data": {
                    "results": [
                        None,
                        9,
                        {"problem_id": 1, "result": ["A", ""]},
                        {"problem_id": 2, "result": "B"},
                        {"result": ["C"]},
                    ]
                }
            }

        monkeypatch.setattr(plat, "_request_json", fake_resp_mixed)
        staged = await plat.get_exam_cached_answers("1", _dummy_course())
        assert staged == {"1": ["A"], "2": ["B"]}

        # 2. data 为 None
        async def fake_resp_null(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"data": None}

        monkeypatch.setattr(plat, "_request_json", fake_resp_null)
        staged_null = await plat.get_exam_cached_answers("1", _dummy_course())
        assert staged_null == {}

        # 3. results 为非列表（如字符串）
        async def fake_resp_invalid(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"data": {"results": "invalid"}}

        monkeypatch.setattr(plat, "_request_json", fake_resp_invalid)
        staged_invalid = await plat.get_exam_cached_answers("1", _dummy_course())
        assert staged_invalid == {}


def test_exam_paper_non_dict_problem_yields_default() -> None:
    """试卷题目列表中非字典元素保留并转换为默认题目，不得静默丢弃或报错。"""
    p_none = BasePlatform._parse_exam_problem(None, 1)
    assert isinstance(p_none, ExamProblemItem)
    assert p_none.index == 1
    assert p_none.id == "1"
    assert p_none.type is None
    assert p_none.prompt == ""
    assert p_none.images == []
    assert p_none.options == []
    assert p_none.score == 0.0
    assert p_none.answer == []
    assert p_none.has_remark is False

    p_int = BasePlatform._parse_exam_problem(999, 2)
    assert p_int.index == 2
    assert p_int.id == "2"


def test_exam_paper_options_filter_non_dict() -> None:
    """题目选项列表跳过非字典项，仅解析有效选项。"""
    prob_raw = PlatformExamProblemRaw.model_validate(
        {
            "problem_id": "100",
            "Options": [None, "string_option", 123, {"key": "A", "value": "<p>选项A</p>"}],
        }
    )
    assert len(prob_raw.Options) == 1
    assert prob_raw.Options[0].key == "A"
    assert prob_raw.Options[0].value == "<p>选项A</p>"


def test_protocol_models_retain_extra_fields() -> None:
    """协议边界模型均使用 extra='allow'，平台未知扩展字段予以完整保留。"""
    cover = PlatformExamCoverData.model_validate({"title": "期末考", "extra_rule": "strict_mode"})
    assert cover.model_extra == {"extra_rule": "strict_mode"}

    token = PlatformExamTokenData.model_validate({"token": "tok123", "client_ip": "127.0.0.1"})
    assert token.model_extra == {"client_ip": "127.0.0.1"}

    cache_item = PlatformExamCacheData.model_validate({"results": [{"problem_id": 1, "result": ["A"], "client_time": 1700000000}]})
    assert cache_item.results[0].model_extra == {"client_time": 1700000000}

    prob = PlatformExamProblemRaw.model_validate({"problem_id": 1, "custom_tag": "math_q"})
    assert prob.model_extra == {"custom_tag": "math_q"}

    res = PlatformExamResult.model_validate({"grade": 90, "extra_metric": 99})
    assert res.model_extra == {"extra_metric": 99}

    notice = PlatformNoticeData.model_validate({"title": "通知", "urgent_level": "high"})
    assert notice.model_extra == {"urgent_level": "high"}


def test_boundary_conversion_filters_unknown_fields_from_domain_output() -> None:
    """验证通过边界模型转换到领域模型时，原始报文中的未知字段不会泄漏到领域模型或序列化输出。"""
    raw_problem = {
        "problem_id": 42,
        "Body": "<p>题目题干</p>",
        "Options": [{"key": "A", "value": "选项A", "unknown_opt_noise": "xyz"}],
        "Answer": ["A"],
        "score": 5.0,
        "unknown_problem_field": "leak_me",
    }
    item = BasePlatform._parse_exam_problem(raw_problem, 1)
    dumped = item.model_dump()
    assert "unknown_problem_field" not in dumped
    assert "unknown_opt_noise" not in dumped["options"][0]
    assert item.score == 5.0
    assert item.id == "42"

    cover_raw = {
        "title": "期中测试",
        "limit": 3600,
        "unknown_platform_flag": True,
        "nested_unknown": {"foo": "bar"},
    }
    cover_data = PlatformExamCoverData.model_validate(cover_raw)
    assert "unknown_platform_flag" in (cover_data.model_extra or {})
    cover_detail = ExamCoverDetail(
        course_id="101",
        exam_id="1",
        title=cover_data.title or "默认",
        problem_count=int(cover_data.problem_count or 0),
        total_score=float(cover_data.total_score or 0.0),
        limit_minutes=int(cover_data.limit or 0) // 60,
        start_time=None,
        deadline=None,
        status="未开始",
        description=cover_data.description or "",
        face_auth=False,
        capture_screen=False,
    )
    detail_dump = cover_detail.model_dump()
    assert "unknown_platform_flag" not in detail_dump
    assert "nested_unknown" not in detail_dump


def test_protocol_models_permissive_field_normalization() -> None:
    """针对原平台报文中的边缘格式（字符串数字、None、布尔真值）的归一化兼容验证。"""
    # 1. 选项 key/value 为 None 或数字
    opt = PlatformExamOptionRaw.model_validate({"key": None, "value": 123})
    assert opt.key == ""
    assert opt.value == "123"

    # 2. 题目分数为数字字符串
    prob = PlatformExamProblemRaw.model_validate({"score": "4.5", "Score": "5"})
    assert prob.score == 4.5
    assert prob.Score == 5.0

    # 3. 成绩字符串在边界模型中被归一为 None（保持原 CLI 逻辑），duration 浮点数截断
    res = PlatformExamResult.model_validate({"grade": "100", "duration": 12.8})
    assert res.grade is None
    assert res.duration == 12

    # 4. 安全标记需严格遵循 Python truthiness，字符串 "0" 为真
    cover = PlatformExamCoverData.model_validate({"open_screen_cuts": "0", "capture_screen": 0})
    assert cover.open_screen_cuts is True
    assert cover.capture_screen is False

    # 5. 公告 title 为显式 None 时回退至 name
    notice = PlatformNoticeData.model_validate({"title": None, "name": "回退名称", "publisher": None, "author": "某老师"})
    assert notice.resolved_title == "回退名称"
    assert notice.resolved_publisher == "某老师"
