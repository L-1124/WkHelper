"""CLI 命令规范与 Schema 契约测试。"""

import json
from io import StringIO

import msgspec
import pytest
from cyclopts import CycloptsError

from wkhelper.cli.app import app
from wkhelper.cli.envelope import create_error_envelope, create_success_envelope, print_json
from wkhelper.cli.spec import COMMAND_REGISTRY, get_command_schema
from wkhelper.core.models import VideoItem


def test_command_registry_completeness():
    """验证所有目标命令均已在规范字典中完整登记。"""
    expected_commands = [
        "status",
        "schema",
        "auth list",
        "auth status",
        "auth login",
        "auth remove",
        "auth storage status",
        "course list",
        "course inspect",
        "video list",
        "video inspect",
        "video learn",
        "homework list",
        "homework inspect",
        "homework submit",
        "question list",
        "question get",
        "question submit",
        "questions export",
    ]
    for cmd in expected_commands:
        assert cmd in COMMAND_REGISTRY, f"缺少命令契约: {cmd}"


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


def test_envelope_encodes_dataclass_data() -> None:
    """信封内的 dataclass 由 msgspec 直接展开，无需手写递归序列化。"""
    envelope = create_success_envelope(data=VideoItem(id="v1", name="测试视频", completed=True))
    payload = json.loads(msgspec.json.encode(envelope))

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
