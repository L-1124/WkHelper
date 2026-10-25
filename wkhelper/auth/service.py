"""账号认证入口。"""

from wkhelper.auth.browser import login_with_browser
from wkhelper.domain.accounts import UserInfo
from wkhelper.platform.base import BasePlatform


async def login_account(
    platform: BasePlatform,
    *,
    cookies: dict[str, str] | None = None,
    timeout: int = 120,
) -> UserInfo:
    """选择显式 Cookie 或官方浏览器登录。"""
    if cookies is not None:
        return await platform.login_with_cookies(cookies)
    return await login_with_browser(platform, timeout=timeout)
