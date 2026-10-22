"""扫码认证编排。"""

import asyncio
import json
from typing import TYPE_CHECKING, Any

from wkhelper.auth.qr import QrCodeSession
from wkhelper.domain.accounts import UserInfo
from wkhelper.domain.errors import AuthError
from wkhelper.infra.http import build_session

if TYPE_CHECKING:
    from wkhelper.platform.base import BasePlatform


async def wait_for_scan(platform: "BasePlatform", qr_session: QrCodeSession, timeout: int) -> dict[str, Any]:
    """通用扫码轮询：连接 WebSocket，推送二维码并等待登录成功回执。"""
    payload = json.dumps(platform.qr_request_payload())
    wait_limit = 60.0
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with build_session(timeout=max(30.0, timeout + 15.0), proxy=None, user_agent=None) as ws_sess:
        async with ws_sess.ws_connect(platform.qr_ws_url(), timeout=max(30.0, timeout + 15.0)) as ws:
            await ws.send_str(payload)
            while loop.time() < deadline:
                remain_time = max(1.0, deadline - loop.time())
                try:
                    raw = await asyncio.wait_for(ws.recv_str(), timeout=min(wait_limit, remain_time))
                except TimeoutError:
                    if loop.time() >= deadline:
                        break
                    await ws.send_str(payload)
                    continue
                except Exception as exc:  # 连接被对端关闭时 curl 会抛传输异常
                    raise AuthError(f"WebSocket 连接已关闭: {exc}") from exc
                if not raw:
                    raise AuthError("WebSocket 连接已关闭")
                message = json.loads(raw)
                hint = platform.qr_expire_seconds(message)
                if hint is not None:
                    wait_limit = hint
                await platform.qr_publish_image(message, qr_session, remain_time)
                if message.get("op") == "loginsuccess":
                    return message
    raise AuthError("扫码超时或未获得有效认证回执")


async def login_with_qrcode(
    platform: "BasePlatform",
    display: str = "image",
    open_browser: bool = False,
    timeout: int = 120,
) -> UserInfo:
    """扫码登录通用流程：展示二维码 → 等待回执 → 换取 Cookie → 校验身份。"""
    qr_session = QrCodeSession(display=display, open_browser=open_browser, platform_name=platform.qr_platform_name)
    await qr_session.start()
    try:
        login_data = await wait_for_scan(platform, qr_session, timeout)
        cookies = await platform.qr_exchange_cookies(login_data)
        user = await platform.login_with_cookies(cookies)
        qr_session.mark_success(name=user.name)
        if qr_session.display == "browser":
            await asyncio.sleep(1.2)
        return user
    except AuthError:
        raise
    except Exception as e:
        raise AuthError(f"扫码通信失败: {e}") from e
    finally:
        await qr_session.close()
