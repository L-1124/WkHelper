"""考场浏览器会话：进入考场时拉起常驻 Chromium，供后续命令复用或关闭。

会话以 JSON 文件持久化端口与临时 profile，跨命令以 CDP 重新连接；
浏览器保持考场页面打开，由页面自身产生心跳（`refresh_time`）与监考采集，
CLI 的作答/交卷仍走 REST 接口。
"""

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from curl_cffi.requests import AsyncSession

from wkhelper.core.browser_cdp import _CdpClient, _find_free_port, _wait_debugger, find_chromium_browser
from wkhelper.core.credential_store import get_default_data_dir

logger = logging.getLogger(__name__)

SESSION_FILENAME = "exam_browser_session.json"


@dataclass
class ExamBrowserSession:
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
    path.write_text(json.dumps(asdict(session), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_session(data_dir: str | Path | None = None, verify: bool = True) -> ExamBrowserSession | None:
    """读取会话；端口已失效时清理并返回 None。"""
    path = session_file(data_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        session = ExamBrowserSession(**raw)
    except (OSError, TypeError, ValueError):
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
        raise RuntimeError("未检测到本地 Chromium 浏览器（Chrome/Edge），无法打开考场会话")

    existing = load_session(data_dir, verify=True)
    if existing is not None:
        await close_exam_browser(existing, data_dir=data_dir)

    port = _find_free_port()
    profile = tempfile.mkdtemp(prefix="wk_exam_profile_")
    cmd = [
        browser,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-extensions",
        "about:blank",
    ]
    kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)

    _browser_ws, page_ws = await _wait_debugger(port)
    if not page_ws:
        shutil.rmtree(profile, ignore_errors=True)
        raise RuntimeError("无法连接到浏览器调试端口")

    async with AsyncSession() as session:
        ws = await session.ws_connect(page_ws)
        cdp = _CdpClient(ws)
        try:
            if cookies:
                payload = [{"name": k, "value": v, "domain": cookie_domain, "path": "/"} for k, v in cookies.items()]
                await cdp.call("Network.setCookies", {"cookies": payload})
            await cdp.call("Page.navigate", {"url": web_url})
        finally:
            await cdp.close()

    record = ExamBrowserSession(
        exam_id=str(exam_id),
        platform=platform,
        host=host,
        eid=eid,
        port=port,
        profile=profile,
        web_url=web_url,
    )
    save_session(record, data_dir)
    return record


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
            async with AsyncSession() as client:
                ws = await client.ws_connect(browser_ws)
                await ws.send_json({"id": 1, "method": "Browser.close"})
                await ws.close()
            closed = True
        except Exception as exc:
            logger.debug(f"关闭考场浏览器失败: {exc}")

    clear_session(session, data_dir)
    return closed
