"""CLI 命令规范与 Schema 契约测试。"""

import json
from io import StringIO
from typing import Any, cast

import pytest
from cyclopts import CycloptsError

from wkhelper.cli.app import app
from wkhelper.cli.envelope import create_error_envelope, create_success_envelope, print_json
from wkhelper.cli.spec import command_registry, get_command_schema, get_command_spec, get_session_options_specs
from wkhelper.domain.video import VideoItem
from wkhelper.infra.serialization import encode_json


def _iter_leaf_commands(app_inst: Any, prefix: str = "") -> list[str]:
    """用 Cyclopts 公开 API（迭代 + 下标访问）枚举叶子业务命令。"""
    hidden = set(app_inst.help_flags) | set(app_inst.version_flags)
    names = [name for name in app_inst if name not in hidden]
    cmds: list[str] = []
    for name in names:
        sub = app_inst[name]
        full = f"{prefix} {name}".strip()
        children = [child for child in sub if child not in (set(sub.help_flags) | set(sub.version_flags))]
        if children:
            cmds.extend(_iter_leaf_commands(sub, full))
        else:
            cmds.append(full)
    return cmds


def test_command_registry_completeness():
    """验证所有在 Cyclopts 注册的业务命令均已在规范字典中完整登记。"""
    registry = command_registry()
    for full_cmd in _iter_leaf_commands(app):
        assert full_cmd in registry, f"缺少命令契约: {full_cmd}"


def test_get_command_schema_top_level():
    """验证不带参数时返回按组索引的概览。"""
    schema = get_command_schema()
    assert schema["type"] == "command_index"
    groups = schema["groups"]
    assert isinstance(groups, dict)
    assert "video" in groups
    assert "homework" in groups


def test_get_command_schema_group():
    """验证输入组名时返回直属命令列表。"""
    schema = get_command_schema("video")
    assert schema["type"] == "group_index"
    assert schema["group"] == "video"
    commands = schema["commands"]
    assert isinstance(commands, list)
    cmds = [c["command"] for c in commands if isinstance(c, dict)]
    assert "video list" in cmds
    assert "video learn" in cmds


def test_get_command_schema_leaf_compact_and_full():
    """验证叶子命令紧凑模式与全量模式契约字段。"""
    compact = get_command_schema("video learn", full=False)
    assert compact["command"] == "video learn"
    assert compact["requires_confirm"] is True
    params = compact["parameters"]
    assert isinstance(params, list)
    for p in params:
        assert isinstance(p, dict)
        assert "name" in p
        assert "flags" in p
        assert "help" not in p

    full = get_command_schema("video learn", full=True)
    assert full["command"] == "video learn"
    full_params = full["parameters"]
    assert isinstance(full_params, list)
    for p in full_params:
        assert isinstance(p, dict)
        assert "help" in p
        assert "type" in p


def test_get_command_spec_compact_and_full():
    """验证 get_command_spec 在紧凑模式与全量模式下的返回规范。"""
    compact = get_command_spec("video learn", full=False)
    assert compact.path == "video learn"
    assert compact.requires_confirm is True
    assert len(compact.arguments) > 0
    for arg in compact.arguments:
        assert arg.name
        assert arg.flags
        assert arg.help == ""
        assert arg.type == ""

    full = get_command_spec("video learn", full=True)
    assert full.path == "video learn"
    assert len(full.arguments) == len(compact.arguments)
    for arg in full.arguments:
        assert arg.name
        assert arg.flags
        assert arg.type != ""

    with pytest.raises(KeyError):
        get_command_spec("unknown command")


def test_schema_collection_argument_video_learn():
    """验证集合类型参数（list[str]）在 Spec 与 Schema 中正确表达 array/items 与 repeat 语义。"""
    spec = get_command_spec("video learn", full=True)
    video_arg = next(arg for arg in spec.arguments if arg.name == "video")
    assert video_arg.type == "array"
    assert video_arg.items == "string"
    assert video_arg.allow_repeating is True
    assert video_arg.positional is True
    assert video_arg.keyword_only is False
    assert video_arg.flags == ["--video", "-v"]
    assert video_arg.negative_flags == ["--empty-video"]

    full_schema = get_command_schema("video learn", full=True)
    parameters = cast(list[dict[str, Any]], full_schema["parameters"])
    video_param = next(p for p in parameters if p["name"] == "video")
    assert video_param["type"] == "array"
    assert video_param["items"] == "string"
    assert video_param["allow_repeating"] is True
    assert video_param["positional"] is True
    assert video_param["keyword_only"] is False
    assert video_param["negative_flags"] == ["--empty-video"]


def test_schema_boolean_and_negative_flags():
    """验证布尔开关及 Cyclopts 自动推导的负向 Flags 正确呈现在 Schema 中。"""
    spec = get_command_spec("video learn", full=True)
    confirm_arg = next(arg for arg in spec.arguments if arg.name == "confirm")
    assert confirm_arg.type == "bool"
    assert confirm_arg.flags == ["--confirm"]
    assert confirm_arg.negative_flags == ["--no-confirm"]
    assert confirm_arg.allow_repeating is False
    assert confirm_arg.default is False

    dry_run_arg = next(arg for arg in spec.arguments if arg.name == "dry_run")
    assert dry_run_arg.type == "bool"
    assert dry_run_arg.flags == ["--dry-run"]
    assert dry_run_arg.negative_flags == ["--no-dry-run"]

    compact_schema = get_command_schema("video learn", full=False)
    parameters = cast(list[dict[str, Any]], compact_schema["parameters"])
    confirm_param = next(p for p in parameters if p["name"] == "confirm")
    assert confirm_param["flags"] == ["--confirm"]
    assert confirm_param["negative_flags"] == ["--no-confirm"]


def test_schema_positional_acceptance():
    """验证命令参数与全局选项在位置参数与关键字参数接受度上的区分。"""
    spec = get_command_spec("video learn", full=True)
    course_arg = next(arg for arg in spec.arguments if arg.name == "course")
    assert course_arg.positional is True
    assert course_arg.keyword_only is False

    # 全局会话选项全部为 keyword_only，禁止位置传参
    global_specs = get_session_options_specs()
    assert len(global_specs) > 0
    for opt in global_specs:
        assert opt.positional is False
        assert opt.keyword_only is True


def test_schema_global_session_options():
    """全局配置可从现有索引和叶子契约发现。"""
    # 1. 顶层索引包含 session_options
    root_schema = get_command_schema()
    assert root_schema["type"] == "command_index"
    assert "session_options" in root_schema
    root_opts = cast(list[dict[str, Any]], root_schema["session_options"])
    opt_names = {opt["name"] for opt in root_opts}
    assert {"format", "data_dir", "credential_mode", "cookie_file", "debug", "user_agent", "proxy"}.issubset(opt_names)
    assert "TOKENS" not in opt_names

    debug_opt = next(opt for opt in root_opts if opt["name"] == "debug")
    assert debug_opt["negative_flags"] == ["--no-debug"]

    # 2. 叶子命令包含 session_options
    leaf_schema = get_command_schema("video learn")
    assert "session_options" in leaf_schema
    leaf_opts = cast(list[dict[str, Any]], leaf_schema["session_options"])
    leaf_opt_names = {opt["name"] for opt in leaf_opts}
    assert leaf_opt_names == opt_names


def test_schema_union_resolution_preserves_alternatives():
    """验证解析 Union/Literal 类型时完整保留所有分支，不丢失候选值。"""
    spec = get_command_spec("video learn", full=True)
    on_error_arg = next(arg for arg in spec.arguments if arg.name == "on_error")
    assert on_error_arg.choices == ["stop", "continue"]

    platform_arg = next(arg for arg in spec.arguments if arg.name == "platform")
    assert platform_arg.choices == ["ykt", "xtzx", "icourse163"]

    schema = get_command_schema("video learn", full=True)
    schema_params = cast(list[dict[str, Any]], schema["parameters"])
    on_error_param = next(p for p in schema_params if p["name"] == "on_error")
    assert on_error_param["choices"] == ["stop", "continue"]

    platform_param = next(p for p in schema_params if p["name"] == "platform")
    assert platform_param["choices"] == ["ykt", "xtzx", "icourse163"]


def test_app_build_and_error():
    """验证 Cyclopts App 正常构建，解析未注册参数时抛出 CycloptsError。"""
    with pytest.raises(CycloptsError):
        app(["unknown-cmd"])


def test_envelope_structure():
    """验证成功与错误信封结构。"""
    succ = create_success_envelope({"count": 10}, meta={"limit": 5})
    assert succ["ok"] is True
    assert succ["schema_version"] == 1
    assert succ["data"] == {"count": 10}
    assert succ["meta"] == {"limit": 5}
    assert succ["next_actions"] == []

    err = create_error_envelope(
        code="TEST_ERROR",
        message="测试错误",
        retryable=True,
        details={"field": "test"},
    )
    assert err["ok"] is False
    err_obj = err["error"]
    assert isinstance(err_obj, dict)
    assert err_obj["code"] == "TEST_ERROR"
    assert err_obj["retryable"] is True
    details = err_obj["details"]
    assert isinstance(details, dict)
    assert details["field"] == "test"


def test_envelope_encodes_model_data() -> None:
    """信封保留模型数据的对外字段。"""
    envelope = create_success_envelope(data=VideoItem(id="v1", name="测试视频", completed=True))
    payload = json.loads(encode_json(envelope))

    assert payload["data"] == {"id": "v1", "name": "测试视频", "completed": True}


def test_print_json_writes_single_line(monkeypatch) -> None:
    """stdout 严格为单个 JSON 行，中文不转义。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    print_json(create_success_envelope(data=VideoItem(id="v1", name="中文", completed=False)))

    text = buf.getvalue()
    assert text.count("\n") == 1
    assert text.endswith("\n")
    assert "中文" in text
    assert json.loads(text)["data"]["name"] == "中文"
