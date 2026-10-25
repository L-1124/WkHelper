"""凭据与账号认证命令。"""

from typing import Annotated, Literal

from cyclopts import App, Parameter

from wkhelper.auth.service import login_account
from wkhelper.cli.context import PlatformCode, get_context
from wkhelper.cli.deps import (
    _PLATFORM_TYPES,
    _create_http_session,
    _open_platform,
    _parse_cookie_payload,
    _read_json_file_or_stdin,
)
from wkhelper.cli.envelope import CommandResult
from wkhelper.domain.accounts import AccountListData, AuthStatusResult, LoginResult, RemoveResult, StorageStatus
from wkhelper.domain.actions import NextAction
from wkhelper.domain.errors import ConfirmationRequiredError

auth_app = App(name="auth", help="凭据与账号认证管理")


@auth_app.command(name="list")
async def auth_list_cmd(
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
) -> CommandResult[AccountListData]:
    """列出已存储的账号摘要。"""
    return CommandResult(data=AccountListData(accounts=get_context().get_store().list_accounts(platform)))


@auth_app.command(name="status")
async def auth_status_cmd(
    platform: Annotated[PlatformCode, Parameter(name=["--platform", "-p"], help="平台代码")],
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[AuthStatusResult]:
    """联网验证指定账号登录态。"""
    async with _open_platform(platform, account) as plat:
        user = plat.user
        return CommandResult(
            data=AuthStatusResult(
                platform=platform,
                user_id=str(user.id if user else ""),
                name=user.name if user else "",
                valid=True,
            )
        )


@auth_app.command(name="login")
async def auth_login_cmd(
    platform: Annotated[PlatformCode, Parameter(name=["--platform", "-p"], help="平台代码")],
    method: Annotated[Literal["browser", "cookie", "qrcode"], Parameter(name=["--method", "-m"], help="登录方式")] = "browser",
    cookie_file: Annotated[str | None, Parameter(name=["--cookie-file"], help="Cookie 文件路径")] = None,
    timeout: Annotated[int, Parameter(name=["--timeout"], help="登录超时秒数")] = 120,
    display: Annotated[
        Literal["image", "browser"] | None, Parameter(name=["--display"], help="[已废弃] 二维码呈现方式", show=False)
    ] = None,
    open_browser: Annotated[bool | None, Parameter(name=["--open-browser"], help="[已废弃] 自动打开浏览器", show=False)] = None,
) -> CommandResult[LoginResult]:
    """账号登录并安全持久化凭据。"""
    if timeout <= 0:
        raise ValueError("`--timeout` 必须为正数")
    _ = (display, open_browser)  # 保留兼容历史输入

    store = get_context().get_store()
    actual_cookie_file = cookie_file or get_context().options.cookie_file
    platform_cls = _PLATFORM_TYPES[platform]

    cookies: dict[str, str] | None = None
    if method == "cookie":
        if not actual_cookie_file:
            raise ValueError("Cookie 登录模式必须指定 `--cookie-file` 参数")
        cookies = _parse_cookie_payload(_read_json_file_or_stdin(actual_cookie_file))

    async with _create_http_session(timeout=15, impersonate=platform_cls.impersonate) as client:
        plat = platform_cls(client)
        user = await login_account(plat, cookies=cookies, timeout=timeout)

        saved_cookies = plat.current_cookies or cookies
        if saved_cookies:
            store.save(platform, user, saved_cookies)

        data = LoginResult(
            platform=platform,
            user_id=str(user.id),
            name=user.name,
            school=user.school,
            persisted=get_context().options.credential_mode == "system",
        )
        next_actions = [
            NextAction(
                reason="查看该账号课程列表",
                argv=["wkhelper", "course", "list", "--platform", platform, "--account", str(user.id)],
            )
        ]
        return CommandResult(data=data, next_actions=next_actions)


@auth_app.command(name="remove")
async def auth_remove_cmd(
    platform: Annotated[PlatformCode, Parameter(name=["--platform", "-p"], help="平台代码")],
    account: Annotated[str, Parameter(name=["--account", "-a"], help="账号 ID")],
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认删除")] = False,
) -> CommandResult[RemoveResult]:
    """删除指定账号凭据。"""
    if not confirm:
        raise ConfirmationRequiredError(
            message="删除账号是本地破坏性操作，必须显式附加 `--confirm` 参数",
            next_actions=[
                NextAction(
                    reason="确认删除",
                    argv=["wkhelper", "auth", "remove", "--platform", platform, "--account", account, "--confirm"],
                )
            ],
        )
    store = get_context().get_store()
    deleted = store.delete(platform, account)
    return CommandResult(data=RemoveResult(platform=platform, account=account, deleted=deleted))


auth_storage_app = auth_app.command(App(name="storage", help="系统存储后端状态"))


@auth_storage_app.command(name="status")
async def auth_storage_status_cmd() -> CommandResult[StorageStatus]:
    """查看系统凭据存储后端状态。"""
    return CommandResult(data=get_context().get_store().get_storage_status())


auth_list = auth_list_cmd
auth_status = auth_status_cmd
auth_login = auth_login_cmd
auth_remove = auth_remove_cmd
auth_storage_status = auth_storage_status_cmd

__all__ = [
    "auth_app",
    "auth_list",
    "auth_list_cmd",
    "auth_login",
    "auth_login_cmd",
    "auth_remove",
    "auth_remove_cmd",
    "auth_status",
    "auth_status_cmd",
    "auth_storage_app",
    "auth_storage_status",
    "auth_storage_status_cmd",
]
