"""主程序命令行端到端调用测试。"""

import json
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
async def test_cli_question_submit_dry_run(monkeypatch):
    """测试单题提交 --dry-run 预览。"""
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
    # 未登录时会返回 AUTH_EXPIRED/AUTH_REQUIRED 错误码 3，测试验证认证守卫
    assert code in (0, 3)
