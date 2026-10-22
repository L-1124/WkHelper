"""计划与参数模型的解析、校验与优先级测试。"""

import pytest

from wkhelper.domain.json_types import JsonValue
from wkhelper.video.engine import VideoWatchOptions
from wkhelper.video.plan import ExecutionOverrides, ExecutionSettings, VideoParameters, VideoPlan, VideoTarget


def test_video_plan_parses_structure() -> None:
    plan = VideoPlan.model_validate(
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
        VideoPlan.model_validate(raw)


def test_parameter_layering_precedence() -> None:
    """内建默认 < 计划级 < 目标级 < 命令行显式覆盖。"""
    plan_defaults = VideoParameters(learning_rate=2.0, completion_threshold=0.8)
    target = VideoParameters(completion_threshold=0.5, video_timeout=90.0)
    cli = VideoParameters(learning_rate=4.0)

    options = VideoWatchOptions().with_parameters(plan_defaults).with_parameters(target).with_parameters(cli)

    assert options.learning_rate == 4.0  # 命令行覆盖计划默认
    assert options.completion_threshold == 0.5  # 目标级覆盖计划默认
    assert options.video_timeout == 90.0  # 目标级
    assert options.heartbeat_interval == 30.0  # 内建默认：每 30 秒批量上报一次
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
        {"video_timeout": 0},
        {"video_timeout": -10},
        {"progress_interval": 0},
        {"stall_timeout": 0},
        {"unknown_param": 1},
    ],
)
def test_video_parameters_reject_invalid_values(raw: dict[str, JsonValue]) -> None:
    with pytest.raises(ValueError):
        VideoParameters.model_validate(raw)


def test_execution_overrides_layering() -> None:
    base = ExecutionSettings(concurrency=3, on_error="continue", total_timeout=900.0)
    effective = ExecutionOverrides(concurrency=1).overlay(base)
    assert effective.concurrency == 1
    assert effective.on_error == "continue"
    assert effective.total_timeout == 900.0

    with pytest.raises(ValueError):
        ExecutionOverrides.model_validate({"on_error": "abort"}).overlay(base)


def test_video_target_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        VideoTarget.model_validate({"course_id": "c_1", "video_id": "v_1", "extra": "x"})


@pytest.mark.parametrize(
    ("field", "value", "location"),
    [
        ("execution", {"concurrency": True}, ("execution", "concurrency")),
        ("schema_version", True, ("schema_version",)),
        ("execution", {"total_timeout": float("inf")}, ("execution", "total_timeout")),
        ("video_defaults", {"video_timeout": float("nan")}, ("video_defaults", "video_timeout")),
        ("video_defaults", {"unexpected": 1}, ("video_defaults", "unexpected")),
        ("targets", [], ("targets",)),
        ("targets", [{"course_id": " ", "video_id": "v"}], ("targets", 0, "course_id")),
        ("targets", [{"course_id": "c", "video_id": " "}], ("targets", 0, "video_id")),
    ],
)
def test_plan_rejects_invalid_fields(field, value, location) -> None:
    from pydantic import ValidationError

    raw = {"targets": [{"course_id": "c", "video_id": "v"}], field: value}
    with pytest.raises(ValidationError) as error:
        VideoPlan.model_validate(raw)
    assert location in {item["loc"] for item in error.value.errors()}


def test_plan_null_defaults_and_local_id_normalization() -> None:
    plan = VideoPlan.model_validate(
        {
            "targets": [{"course_id": " c ", "video_id": " v ", "name": " 原名 "}],
            "execution": None,
            "video_defaults": None,
        }
    )
    assert plan.execution == ExecutionSettings()
    assert plan.video_defaults == VideoParameters()
    assert (plan.targets[0].course_id, plan.targets[0].video_id, plan.targets[0].name) == ("c", "v", " 原名 ")
    assert VideoParameters(video_timeout=None).video_timeout is None


def test_invalid_construction_and_assignment_preserve_valid_state() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        VideoParameters(video_timeout=0)  # type: ignore[bad-argument-type]
    settings = ExecutionSettings(concurrency=2)
    with pytest.raises(ValidationError):
        settings.concurrency = 0
    assert settings.concurrency == 2
    plan = VideoPlan(targets=[VideoTarget(course_id="c", video_id="v")])
    with pytest.raises(ValidationError):
        plan.execution.concurrency = 0
    assert plan.execution.concurrency == 1
    with pytest.raises(ValidationError):
        plan.video_defaults.video_timeout = -1
    assert plan.video_defaults.model_dump()["video_timeout"] is None


def test_input_defaults_are_validated() -> None:
    from pydantic import Field, ValidationError

    class InvalidSettings(ExecutionSettings):
        concurrency: int = Field(default=0, ge=1)  # type: ignore[bad-argument-type]

    with pytest.raises(ValidationError):
        InvalidSettings()
