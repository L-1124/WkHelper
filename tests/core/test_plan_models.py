"""计划与参数模型的解析、校验与优先级测试。"""

import pytest

from wkhelper.core.models import (
    ExecutionOverrides,
    ExecutionSettings,
    JsonValue,
    VideoParameters,
    VideoPlan,
    VideoTarget,
)
from wkhelper.core.video import VideoWatchOptions


def test_video_plan_parses_structure() -> None:
    plan = VideoPlan.from_mapping(
        {
            "schema_version": 1,
            "platform": "ykt",
            "account": "u_1",
            "execution": {"concurrency": 2, "on_error": "continue", "total_timeout": 600},
            "video_defaults": {"completion_threshold": 0.9, "video_timeout": 120},
            "targets": [
                {"course_id": "c_1", "video_id": "v_1"},
                {"course_id": "c_1", "video_id": "v_2", "parameters": {"video_timeout": 300}},
            ],
        }
    )

    assert plan.platform == "ykt"
    assert plan.account == "u_1"
    assert plan.execution.concurrency == 2
    assert plan.execution.on_error == "continue"
    assert plan.execution.total_timeout == 600.0
    assert plan.video_defaults.completion_threshold == 0.9
    assert plan.video_defaults.video_timeout == 120.0
    assert [target.video_id for target in plan.targets] == ["v_1", "v_2"]
    assert plan.targets[1].parameters is not None
    assert plan.targets[1].parameters.video_timeout == 300.0


@pytest.mark.parametrize(
    "raw",
    [
        {"targets": [{"course_id": "c_1", "video_id": "v_1", "unknown": 1}]},
        {"targets": [], "unexpected": True},
        {"targets": [{"course_id": "c_1"}]},
        {"schema_version": 2, "targets": [{"course_id": "c_1", "video_id": "v_1"}]},
    ],
)
def test_video_plan_rejects_invalid_input(raw: dict[str, JsonValue]) -> None:
    with pytest.raises(ValueError):
        VideoPlan.from_mapping(raw)


def test_parameter_layering_precedence() -> None:
    """内建默认 < 计划级 < 目标级 < 命令行显式覆盖。"""
    plan_defaults = VideoParameters(learning_rate=2.0, completion_threshold=0.8)
    target = VideoParameters(completion_threshold=0.5, video_timeout=90.0)
    cli = VideoParameters(learning_rate=4.0)

    options = VideoWatchOptions().with_parameters(plan_defaults).with_parameters(target).with_parameters(cli)

    assert options.learning_rate == 4.0  # 命令行覆盖计划默认
    assert options.completion_threshold == 0.5  # 目标级覆盖计划默认
    assert options.video_timeout == 90.0  # 目标级
    assert options.heartbeat_interval == 1.5  # 内建默认
    assert options.jitter == 0.2  # 内建默认
    assert options.video_delay == 3.0  # 内建默认


@pytest.mark.parametrize(
    "raw",
    [
        {"learning_rate": 0},
        {"heartbeat_interval": -1},
        {"completion_threshold": 1.5},
        {"completion_threshold": 0},
        {"max_heartbeats": 0},
        {"jitter": 1.5},
        {"jitter": -0.1},
        {"video_delay": -1},
        {"unknown_param": 1},
    ],
)
def test_video_parameters_reject_invalid_values(raw: dict[str, JsonValue]) -> None:
    with pytest.raises(ValueError):
        VideoParameters.from_mapping(raw)


def test_execution_overrides_layering() -> None:
    base = ExecutionSettings(concurrency=3, on_error="continue", total_timeout=900.0)
    effective = ExecutionOverrides(concurrency=1).overlay(base)
    assert effective.concurrency == 1
    assert effective.on_error == "continue"
    assert effective.total_timeout == 900.0

    with pytest.raises(ValueError):
        ExecutionOverrides(on_error="abort").overlay(base)


def test_video_target_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        VideoTarget.from_mapping({"course_id": "c_1", "video_id": "v_1", "extra": "x"})
