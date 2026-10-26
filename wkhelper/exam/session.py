"""考场浏览器会话：进入考场时拉起常驻 Chromium，供后续命令复用或关闭。

会话以 JSON 文件持久化端口与临时 profile，跨命令以 CDP 重新连接；
浏览器保持考场页面打开，由页面自身产生心跳（`refresh_time`）与监考采集，
CLI 的作答通过在页面中模拟点击与输入完成，交卷由本人在浏览器中提交。
"""

import json
import logging
import shutil
import urllib.request
from pathlib import Path

from pydantic import ValidationError

from wkhelper.domain.errors import APIError
from wkhelper.domain.model import Model
from wkhelper.infra.browser.cdp import CdpClient
from wkhelper.infra.browser.process import BrowserManager, find_chromium_browser, wait_debugger
from wkhelper.infra.credentials import get_default_data_dir
from wkhelper.infra.http import build_session

logger = logging.getLogger(__name__)

SESSION_FILENAME = "exam_browser_session.json"


class ExamBrowserSession(Model):
    """考场浏览器会话描述。"""

    exam_id: str
    platform: str
    host: str
    eid: int
    port: int
    profile: str
    web_url: str


def session_file(data_dir: str | Path | None = None) -> Path:
    """会话文件路径（跟随 `--data-dir`）。"""
    base = Path(data_dir) if data_dir else get_default_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / SESSION_FILENAME


def _port_alive(port: int) -> bool:
    """调试端口是否仍可用。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as resp:
            return bool(resp.status == 200)
    except Exception:
        return False


def save_session(session: ExamBrowserSession, data_dir: str | Path | None = None) -> Path:
    """持久化会话并返回文件路径。"""
    path = session_file(data_dir)
    path.write_text(session.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_session(data_dir: str | Path | None = None, verify: bool = True) -> ExamBrowserSession | None:
    """读取会话；端口已失效时清理并返回 None。"""
    path = session_file(data_dir)
    if not path.exists():
        return None
    try:
        session = ExamBrowserSession.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if verify and not _port_alive(session.port):
        clear_session(session, data_dir)
        return None
    return session


def clear_session(session: ExamBrowserSession | None, data_dir: str | Path | None = None) -> None:
    """删除会话文件与临时 profile。"""
    session_file(data_dir).unlink(missing_ok=True)
    if session and session.profile:
        shutil.rmtree(session.profile, ignore_errors=True)


async def open_exam_browser(
    *,
    exam_id: str,
    platform: str,
    host: str,
    eid: int,
    web_url: str,
    cookies: dict[str, str],
    cookie_domain: str = "examination.xuetangx.com",
    data_dir: str | Path | None = None,
) -> ExamBrowserSession:
    """以独立 profile 拉起常驻 Chromium，注入会话 Cookie 并打开考场页。"""
    browser = find_chromium_browser()
    if not browser:
        raise APIError("未检测到本地 Chromium 浏览器（Chrome/Edge），无法打开考场会话", retryable=False)

    existing = load_session(data_dir, verify=True)
    if existing is not None:
        await close_exam_browser(existing, data_dir=data_dir)
    b_proc = BrowserManager.launch_browser(mode="headful", detached=True)
    persisted = False
    try:
        port = b_proc.port
        profile = b_proc.profile_dir
        _browser_ws, page_ws = await wait_debugger(port)
        if not page_ws:
            raise APIError(
                "考场浏览器已拉起但调试端口未就绪，无法接管考场页面。"
                "请关闭残留的 WkHelper 浏览器窗口后重试；若持续失败，检查本机 Chromium 是否禁止远程调试。",
                retryable=True,
            )

        async with build_session(timeout=15.0, proxy=None, user_agent=None, impersonate=None) as session:
            ws = await session.ws_connect(page_ws)
            cdp = CdpClient(ws)
            try:
                if cookies:
                    payload = [{"name": k, "value": v, "domain": cookie_domain, "path": "/"} for k, v in cookies.items()]
                    await cdp.call("Network.setCookies", {"cookies": payload})
                await cdp.call("Page.navigate", {"url": web_url})
            finally:
                await cdp.close()

        record = ExamBrowserSession(
            exam_id=exam_id,
            platform=platform,
            host=host,
            eid=eid,
            port=port,
            profile=profile,
            web_url=web_url,
        )
        save_session(record, data_dir)
        persisted = True
        return record
    finally:
        if not persisted:
            BrowserManager.close_browser(b_proc)


async def close_exam_browser(session: ExamBrowserSession, data_dir: str | Path | None = None) -> bool:
    """关闭会话浏览器并清理会话文件与临时 profile。"""
    closed = False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{session.port}/json/version", timeout=1) as resp:
            browser_ws = json.loads(resp.read()).get("webSocketDebuggerUrl")
    except Exception:
        browser_ws = None

    if browser_ws:
        try:
            async with build_session(timeout=15.0, proxy=None, user_agent=None, impersonate=None) as client:
                ws = await client.ws_connect(browser_ws)
                await ws.send_json({"id": 1, "method": "Browser.close"})
                await ws.close()
            closed = True
        except Exception as exc:
            logger.debug(f"关闭考场浏览器失败: {exc}")

    clear_session(session, data_dir)
    return closed


async def browser_cookies(session: ExamBrowserSession) -> dict[str, str]:
    """读取活跃浏览器会话的 Cookie，供考场只读请求复用，保持与浏览器会话一致。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{session.port}/json/list", timeout=1) as resp:
            tabs = json.loads(resp.read())
    except Exception:
        return {}
    page = next((t for t in tabs if isinstance(t, dict) and t.get("type") == "page" and t.get("webSocketDebuggerUrl")), None)
    if not page:
        return {}

    cookies: dict[str, str] = {}
    try:
        async with build_session(timeout=15.0, proxy=None, user_agent=None, impersonate=None) as client:
            ws = await client.ws_connect(str(page["webSocketDebuggerUrl"]))
            cdp = CdpClient(ws)
            try:
                res = await cdp.call("Network.getCookies", {"urls": [session.web_url]})
                for item in res.get("cookies", []):
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        cookies[str(item["name"])] = str(item["value"])
            finally:
                await cdp.close()
    except Exception as exc:
        logger.debug(f"读取浏览器 Cookie 失败: {exc}")
    return cookies
