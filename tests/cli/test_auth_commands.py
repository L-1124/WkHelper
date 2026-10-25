"""CLI 凭据与账号认证命令集成测试。

覆盖 ykt、xtzx、icourse163 三平台的官方浏览器登录、Cookie 文件导入登录、
历史参数向后兼容（qrcode/display/open-browser）以及状态自省。
"""

import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from wkhelper.cli.context import ExecutionContext
from wkhelper.domain.accounts import UserInfo
from wkhelper.infra.credentials import CredentialStore
from wkhelper.main import async_main
from wkhelper.platform.base import BasePlatform


@pytest.fixture
def credential_store(monkeypatch: pytest.MonkeyPatch) -> CredentialStore:
    store = CredentialStore(credential_mode="session")
    monkeypatch.setattr(ExecutionContext, "get_store", lambda self: store)
    return store


@pytest.mark.parametrize("platform", ["ykt", "xtzx", "icourse163"])
@pytest.mark.parametrize("method_args", [[], ["-m", "browser", "--timeout", "45"]])
@pytest.mark.asyncio
async def test_auth_login_browser_default_three_platforms(
    platform: str, method_args: list[str], monkeypatch: pytest.MonkeyPatch, credential_store: CredentialStore
) -> None:
    """默认不传 -m 时采用 browser 方式登录，三个平台均正确调用 login_account 并持久化。"""
    captured_calls: list[dict[str, Any]] = []

    async def fake_login_account(plat: BasePlatform, *, cookies: dict[str, str] | None = None, timeout: int = 120) -> UserInfo:
        captured_calls.append({"platform": platform, "cookies": cookies, "timeout": timeout})
        plat.current_cookies = {"token": f"token_{platform}_123", "sess": "abc"}
        return UserInfo(id=f"uid_{platform}", name=f"User_{platform}", school="测试大学")

    monkeypatch.setattr("wkhelper.cli.commands.auth.login_account", fake_login_account)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--credential-mode", "session", "auth", "login", "-p", platform, *method_args])
    assert code == 0

    output = json.loads(buf.getvalue().strip())
    assert output["ok"] is True
    data = output["data"]
    assert data["platform"] == platform
    assert data["user_id"] == f"uid_{platform}"
    assert data["name"] == f"User_{platform}"
    assert data["school"] == "测试大学"
    assert data["persisted"] is False  # session 模式

    assert len(captured_calls) == 1
    assert captured_calls[0]["cookies"] is None

    # 验证凭据已正确存入 store
    store = credential_store
    accounts = store.list_accounts(platform)
    assert len(accounts) == 1
    assert accounts[0].user_id == f"uid_{platform}"
    assert accounts[0].name == f"User_{platform}"


@pytest.mark.parametrize("platform", ["ykt", "xtzx", "icourse163"])
@pytest.mark.asyncio
async def test_auth_login_cookie_file_three_platforms(
    platform: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, credential_store: CredentialStore
) -> None:
    """三个平台均支持通过 --method cookie 与 --cookie-file 导入凭据登录。"""
    cookie_data = {"sessionid": f"sess_{platform}", "csrftoken": f"csrf_{platform}"}
    cookie_file = tmp_path / f"cookies_{platform}.json"
    cookie_file.write_text(json.dumps(cookie_data), encoding="utf-8")

    captured_calls: list[dict[str, Any]] = []

    async def fake_login_account(plat: BasePlatform, *, cookies: dict[str, str] | None = None, timeout: int = 120) -> UserInfo:
        captured_calls.append({"cookies": cookies, "timeout": timeout})
        plat.current_cookies = cookies
        return UserInfo(id=f"cookie_user_{platform}", name=f"Cookie_{platform}")

    monkeypatch.setattr("wkhelper.cli.commands.auth.login_account", fake_login_account)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(
        [
            "--credential-mode",
            "session",
            "auth",
            "login",
            "-p",
            platform,
            "-m",
            "cookie",
            "--cookie-file",
            str(cookie_file),
        ]
    )
    assert code == 0
    assert len(captured_calls) == 1
    assert captured_calls[0]["cookies"] == cookie_data

    store = credential_store
    accounts = store.list_accounts(platform)
    assert any(a.user_id == f"cookie_user_{platform}" for a in accounts)


@pytest.mark.parametrize(
    "extra_args",
    [
        ["-m", "qrcode"],
        ["-m", "qrcode", "--display", "browser", "--open-browser"],
        ["-m", "qrcode", "--display", "image"],
        ["--display", "browser", "--open-browser"],
    ],
)
@pytest.mark.asyncio
async def test_auth_login_legacy_qrcode_and_display_flags_graceful(
    extra_args: list[str], monkeypatch: pytest.MonkeyPatch, credential_store: CredentialStore
) -> None:
    """优雅兼容历史命令参数（--method qrcode, --display, --open-browser），自动路由至 browser 登录。"""
    captured_calls: list[dict[str, Any]] = []

    async def fake_login_account(plat: BasePlatform, *, cookies: dict[str, str] | None = None, timeout: int = 120) -> UserInfo:
        captured_calls.append({"cookies": cookies, "timeout": timeout})
        plat.current_cookies = {"sessionid": "legacy_ok"}
        return UserInfo(id="legacy_user", name="LegacyUser")

    monkeypatch.setattr("wkhelper.cli.commands.auth.login_account", fake_login_account)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    cmd = ["--credential-mode", "session", "auth", "login", "-p", "ykt", *extra_args]
    code = await async_main(cmd)
    assert code == 0
    assert len(captured_calls) == 1
    assert captured_calls[0]["cookies"] is None
    output = json.loads(buf.getvalue())
    assert output["data"]["user_id"] == "legacy_user"
    assert credential_store.list_accounts("ykt")[0].user_id == "legacy_user"


@pytest.mark.parametrize("bad_timeout", ["0", "-1", "-120"])
@pytest.mark.asyncio
async def test_auth_login_invalid_timeout_rejected(bad_timeout: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """非正数超时被拦截并返回错误信封。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["auth", "login", "-p", "ykt", "--timeout", bad_timeout])
    assert code == 2
    data = json.loads(buf.getvalue().strip())
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_auth_login_cookie_without_file_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cookie 登录模式未指定 --cookie-file 时被拦截。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["auth", "login", "-p", "ykt", "-m", "cookie"])
    assert code == 2
    data = json.loads(buf.getvalue().strip())
    assert data["ok"] is False
    assert "cookie-file" in str(data["error"]["message"])


@pytest.mark.asyncio
async def test_auth_status_calls_login_with_cookies(monkeypatch: pytest.MonkeyPatch, credential_store: CredentialStore) -> None:
    """auth status 经过 _open_platform 调用 plat.login_with_cookies 进行有效性核验。"""
    from wkhelper.platform.yuketang import YuketangPlatform

    login_with_cookies_called: list[dict[str, str]] = []

    async def fake_login_with_cookies(self: YuketangPlatform, cookies: dict[str, str]) -> UserInfo:
        login_with_cookies_called.append(cookies)
        self.user = UserInfo(id="stored_user_1", name="StoredName")
        return self.user

    monkeypatch.setattr(YuketangPlatform, "login_with_cookies", fake_login_with_cookies)

    # 预先在 session 存储中塞入账号凭据
    store = credential_store
    store.save("ykt", UserInfo(id="stored_user_1", name="StoredName"), {"sessionid": "s1", "csrftoken": "c1"})

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--credential-mode", "session", "auth", "status", "-p", "ykt"])
    assert code == 0
    output = json.loads(buf.getvalue().strip())
    assert output["ok"] is True
    assert output["data"]["valid"] is True
    assert output["data"]["user_id"] == "stored_user_1"
    assert len(login_with_cookies_called) == 1
    assert login_with_cookies_called[0] == {"sessionid": "s1", "csrftoken": "c1"}
