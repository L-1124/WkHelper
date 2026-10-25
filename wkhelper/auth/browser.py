"""受管浏览器登录与 Cookie 捕获。"""

import asyncio
from contextlib import suppress
from typing import Literal

from wkhelper.domain.accounts import UserInfo
from wkhelper.domain.errors import AuthError
from wkhelper.infra.browser.cdp import CdpClient
from wkhelper.infra.browser.process import BrowserManager
from wkhelper.infra.events import notifier
from wkhelper.infra.http import build_session
from wkhelper.platform.base import BasePlatform

_notify = notifier(None)


async def login_with_browser(
    platform: BasePlatform,
    *,
    timeout: int = 120,
    mode: Literal["headful", "headless"] = "headful",
) -> UserInfo:
    """打开官方登录页，仅在服务端验证通过后接受浏览器凭据。"""
    if timeout <= 0:
        raise ValueError("登录超时必须为正数")
    try:
        async with asyncio.timeout(timeout):
            async with BrowserManager.acquire_session(mode=mode) as b_proc:
                if not b_proc.page_ws:
                    raise AuthError("无法连接到浏览器调试端口")
                async with build_session(timeout=15.0, proxy=None, user_agent=None, impersonate=None) as session:
                    ws = await session.ws_connect(b_proc.page_ws)
                    cdp = CdpClient(ws)
                    try:
                        await cdp.call("Page.enable")
                        await cdp.call("Page.navigate", {"url": platform.login_url})
                        _notify("auth.challenge", {"url": platform.login_url, "timeout": timeout, "mode": mode})
                        while True:
                            if b_proc.proc.poll() is not None or not cdp.alive:
                                raise AuthError("用户关闭了浏览器窗口，登录取消")
                            result = await cdp.call("Network.getCookies", {"urls": [platform.login_url]})
                            cookies = {item["name"]: item["value"] for item in result.get("cookies", [])}
                            if all(cookies.get(name) for name in platform.essential_cookies):
                                try:
                                    user = await platform.login_with_cookies(cookies)
                                except AuthError:
                                    # 登录页可能先设置匿名 Cookie，继续等待用户完成认证。
                                    pass
                                else:
                                    platform.current_cookies = cookies
                                    return user
                            await asyncio.sleep(1.0)
                    except Exception as exc:
                        if not cdp.alive:
                            raise AuthError("用户关闭了浏览器窗口，登录取消") from exc
                        raise
                    finally:
                        try:
                            if cdp.alive:
                                # 先正常退出，释放 Windows 下的 profile 文件锁。
                                with suppress(Exception):
                                    await cdp.call("Browser.close", timeout=3.0)
                                    await asyncio.to_thread(b_proc.proc.wait, timeout=3.0)
                        finally:
                            await cdp.close()
    except TimeoutError:
        raise AuthError(f"登录超时 ({timeout}s)，未检测到有效登录态") from None
