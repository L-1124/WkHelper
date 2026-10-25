"""主程序命令行端到端调用测试。"""

import json
from contextlib import asynccontextmanager
from io import StringIO

import pytest

from wkhelper.main import async_main


@pytest.mark.asyncio
async def test_cli_status_command(monkeypatch):
    """测试 wkhelper status 命令输出符合规范。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--credential-mode", "session", "status"])
    assert code == 0

    output = buf.getvalue().strip()
    data = json.loads(output)
    assert data["ok"] is True
    assert data["schema_version"] == 1
    assert "credential_storage" in data["data"]
    assert "accounts_count" in data["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("argv", [["--help"], ["exam", "--help"], ["video", "list", "--help"]])
async def test_cli_help_flag_returns_zero(argv, capsys) -> None:
    code = await async_main(argv)
    assert code == 0
    out = capsys.readouterr().out
    assert "Usage:" in out


@pytest.mark.asyncio
async def test_cli_schema_command(monkeypatch):
    """测试 wkhelper schema video learn 命令输出。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["schema", "video learn"])
    assert code == 0

    output = buf.getvalue().strip()
    data = json.loads(output)
    assert data["ok"] is True
    assert data["data"]["command"] == "video learn"


@pytest.mark.asyncio
async def test_cli_auth_storage_status_command(monkeypatch):
    """测试 wkhelper auth storage status 命令输出。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--credential-mode", "session", "auth", "storage", "status"])
    assert code == 0
    output = buf.getvalue().strip()
    data = json.loads(output)
    assert data["ok"] is True
    assert data["data"]["mode"] == "session"


@pytest.mark.asyncio
async def test_cli_requires_confirmation_exit_code(monkeypatch):
    """测试写操作缺少 --confirm 时返回退出码 5。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--credential-mode", "session", "auth", "remove", "-p", "ykt", "-a", "12345"])
    assert code == 5

    output = buf.getvalue().strip()
    data = json.loads(output)
    assert data["ok"] is False
    assert data["error"]["code"] == "CONFIRMATION_REQUIRED"


@pytest.mark.asyncio
async def test_cli_discussion_delete_requires_confirm(monkeypatch):
    """测试删除讨论发言缺少 --confirm 时返回退出码 5。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--credential-mode", "session", "discussion", "delete", "-c", "1001", "-m", "8888"])
    assert code == 5

    output = buf.getvalue().strip()
    data = json.loads(output)
    assert data["ok"] is False
    assert data["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert any("--confirm" in act["argv"] for act in data.get("next_actions", []))


@pytest.mark.asyncio
async def test_cli_discussion_delete_dry_run(monkeypatch):
    """测试删除讨论发言 --dry-run 预览无需认证。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--credential-mode", "session", "discussion", "delete", "-c", "1001", "-m", "8888", "--dry-run"])
    assert code == 0

    output = buf.getvalue().strip()
    data = json.loads(output)
    assert data["ok"] is True
    assert data["data"]["comment_id"] == "8888"
    assert "[dry-run]" in data["data"]["message"]


@pytest.mark.asyncio
async def test_cli_invalid_argument_envelope(monkeypatch):
    """测试非法参数时输出 JSON 格式的错误信封并退出码 2。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["auth", "login", "--platform", "invalid_platform"])
    assert code == 2

    output = buf.getvalue().strip()
    assert output != ""
    data = json.loads(output)
    assert data["ok"] is False
    assert data["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_cli_question_submit_auth_guard(monkeypatch):
    """测试未登录状态下提交题目被认证守卫拦截（退出码 3）。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(
        [
            "--credential-mode",
            "session",
            "question",
            "submit",
            "--platform",
            "ykt",
            "--account",
            "u1",
            "--course",
            "1",
            "--homework",
            "hw1",
            "--question",
            "q1",
            "--answer",
            "A,B",
            "--dry-run",
        ]
    )
    assert code == 3
    data = json.loads(buf.getvalue().strip())
    assert data["ok"] is False
    assert data["error"]["code"] == "AUTH_EXPIRED"


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"q": ["A", None, True, 7, 2.5]}, {"q": ["A", "7", "2.5"]}),
        ({"answers": [{"question_id": "q", "answer": "A", "extra": 1}, {"question_id": "q", "answer": ["C"]}]}, {"q": ["C"]}),
        ({"answers": "A"}, {"answers": ["A"]}),
    ],
)
def test_answer_input_preserves_compatible_normalization(payload, expected) -> None:
    from wkhelper.cli.deps import _parse_answers_payload

    assert _parse_answers_payload(payload) == expected


@pytest.mark.asyncio
async def test_internal_model_failure_is_not_a_user_argument_error(monkeypatch, capsys) -> None:
    from wkhelper.cli.commands import course as command
    from wkhelper.domain.courses import CourseListData

    @asynccontextmanager
    async def invalid_platform(*args, **kwargs):
        CourseListData.model_validate({"courses": "invalid"})
        yield

    monkeypatch.setattr(command, "_open_platform", invalid_platform)
    code = await async_main(["--format", "json", "--credential-mode", "session", "course", "list"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 1
    assert envelope["error"]["code"] == "INTERNAL_ERROR"
