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
async def test_global_proxy_and_user_agent_flags(monkeypatch):
    """测试全局 --proxy 与 --user-agent 由 meta 应用解析并写入上下文。"""
    from wkhelper.cli.app import ctx

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--proxy", "http://127.0.0.1:8080", "--user-agent", "CustomUA/1.0", "status"])
    assert code == 0
    assert ctx.proxy == "http://127.0.0.1:8080"
    assert ctx.user_agent == "CustomUA/1.0"


@pytest.mark.asyncio
async def test_global_flags_after_subcommand(monkeypatch):
    """全局参数置于子命令之后同样生效（fallthrough 解析）。"""
    from wkhelper.cli.app import ctx

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["status", "--debug", "--credential-mode", "session"])
    assert code == 0
    assert ctx.debug is True
    assert ctx.credential_mode == "session"


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
