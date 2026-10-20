"""基于 Chrome DevTools Protocol (CDP) 的交互式人机验证穿透模块。

浏览器由本模块以独立临时 profile 拉起；用户手动完成滑块后，
本模块从浏览器网络事件中截获目标接口响应，并同步最新 Cookie。
"""

import asyncio
import json
import logging
from typing import Any

from curl_cffi.requests import AsyncSession

from wkhelper.core.browser_manager import (
    BrowserManager,
    BrowserProcess,
    CdpClient,
    _CdpClient,
    _find_free_port,
    _wait_debugger,
    find_chromium_browser,
)
from wkhelper.core.events import notifier
from wkhelper.core.exceptions import RiskControlError

logger = logging.getLogger(__name__)

_notify = notifier(None)


async def _close_browser(browser_ws: str | None, proc: Any | None) -> None:
    if browser_ws:
        try:
            async with AsyncSession() as session:
                ws = await session.ws_connect(browser_ws)
                await ws.send_json({"id": 1, "method": "Browser.close"})
                await ws.close()
        except Exception:
            pass
    if proc is not None and getattr(proc, "poll", None) is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except Exception:
            proc.kill()


async def solve_captcha_interactive(
    web_url: str,
    target_api_pattern: str,
    cookies: dict[str, str],
    cookie_domain: str = ".xuetangx.com",
    timeout: float = 180.0,
) -> tuple[dict[str, Any], dict[str, str]]:
    """拉起独立 Chromium 窗口供用户完成滑块人机验证，并自动截获接口响应数据与新鲜 Cookie。"""
    browser_bin = find_chromium_browser()
    if not browser_bin:
        raise RiskControlError("未检测到本地 Chromium 浏览器（Chrome/Edge），无法拉起图形化滑块验证", web_url=web_url)

    _notify(
        "security.captcha_challenge",
        {
            "web_url": web_url,
            "browser": browser_bin,
            "timeout": timeout,
        },
    )

    async with BrowserManager.acquire_session(mode="headful", cookies=cookies, cookie_domain=cookie_domain) as b_proc:
        page_ws = b_proc.page_ws
        if not page_ws:
            raise RiskControlError("无法连接到浏览器调试端口", web_url=web_url)

        async with AsyncSession() as session:
            ws = await session.ws_connect(page_ws)
            cdp = _CdpClient(ws)
            try:
                await cdp.call("Network.enable")
                await cdp.call("Page.navigate", {"url": web_url})

                start = asyncio.get_running_loop().time()
                captured: dict[str, Any] | None = None
                scanned = 0
                while asyncio.get_running_loop().time() - start < timeout:
                    if not cdp.alive:
                        raise RiskControlError("用户关闭了浏览器窗口，验证未完成", web_url=web_url)

                    while scanned < len(cdp.events):
                        ev = cdp.events[scanned]
                        scanned += 1
                        if ev.get("method") != "Network.responseReceived":
                            continue
                        params = ev.get("params", {})
                        resp = params.get("response", {})
                        if target_api_pattern not in str(resp.get("url", "")) or int(resp.get("status", 0)) != 200:
                            continue
                        req_id = params.get("requestId")
                        if not req_id:
                            continue
                        try:
                            body = await cdp.call("Network.getResponseBody", {"requestId": req_id})
                            parsed = json.loads(body.get("body", "") or "{}")
                        except Exception as exc:
                            logger.debug(f"读取响应体失败: {exc}")
                            continue
                        if isinstance(parsed, dict) and parsed.get("success") is True and parsed.get("data") is not None:
                            captured = parsed
                            break

                    if captured:
                        break
                    await asyncio.sleep(0.3)

                if captured is None:
                    raise RiskControlError(f"等待用户完成人机验证超时 ({int(timeout)} 秒)", web_url=web_url)

                fresh: dict[str, str] = {}
                try:
                    res = await cdp.call("Network.getCookies", {"urls": [web_url]})
                    for item in res.get("cookies", []):
                        if isinstance(item, dict) and "name" in item and "value" in item:
                            fresh[str(item["name"])] = str(item["value"])
                except Exception:
                    pass

                _notify(
                    "security.captcha_solved",
                    {"web_url": web_url, "cookie_names": sorted(fresh)},
                )
                return captured, fresh
            finally:
                await cdp.close()


__all__ = [
    "BrowserManager",
    "BrowserProcess",
    "CdpClient",
    "_CdpClient",
    "_close_browser",
    "_find_free_port",
    "_wait_debugger",
    "find_chromium_browser",
    "solve_captcha_interactive",
]
