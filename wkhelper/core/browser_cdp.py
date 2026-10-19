"""基于 Chrome DevTools Protocol (CDP) 的交互式人机验证穿透模块。

浏览器由本模块以独立临时 profile 拉起；用户手动完成滑块后，
本模块从浏览器网络事件中截获目标接口响应，并同步最新 Cookie。
"""

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
from typing import Any

from curl_cffi.requests import AsyncSession

from wkhelper.core.events import notifier
from wkhelper.core.exceptions import RiskControlError

logger = logging.getLogger(__name__)

_notify = notifier(None)


def _find_free_port() -> int:
    """获取本地可用随机端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_chromium_browser() -> str | None:
    """按优先级探测系统已安装的 Chromium 浏览器可执行文件路径。"""
    candidates: list[str] = []
    if sys.platform == "win32":
        for base in (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LocalAppData", ""),
        ):
            if not base:
                continue
            candidates.append(os.path.join(base, r"Google\Chrome\Application\chrome.exe"))
            candidates.append(os.path.join(base, r"Microsoft\Edge\Application\msedge.exe"))
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
            path = shutil.which(name)
            if path:
                candidates.append(path)

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class _CdpClient:
    """轻量 CDP WebSocket 异步客户端与事件分发器。"""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: list[dict[str, Any]] = []
        self.alive = True
        self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        try:
            while True:
                msg = await self.ws.recv_json()
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(str(msg["error"].get("message") or msg["error"])))
                        else:
                            fut.set_result(msg.get("result", {}))
                elif "method" in msg:
                    self.events.append(msg)
        except Exception:
            pass
        finally:
            self.alive = False

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send_json({"id": req_id, "method": method, "params": params or {}})
        return await fut

    async def close(self) -> None:
        self._pump_task.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass


def _http_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=1) as resp:
        return json.loads(resp.read())


async def _wait_debugger(port: int, timeout: float = 8.0) -> tuple[str | None, str | None]:
    """等待 DevTools 端口就绪，返回 (browser_ws, page_ws)。"""
    browser_ws: str | None = None
    page_ws: str | None = None
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.2)
        try:
            if browser_ws is None:
                version = await asyncio.to_thread(_http_json, f"http://127.0.0.1:{port}/json/version")
                browser_ws = version.get("webSocketDebuggerUrl") if isinstance(version, dict) else None
            tabs = await asyncio.to_thread(_http_json, f"http://127.0.0.1:{port}/json/list")
            page_tab = next(
                (t for t in tabs if isinstance(t, dict) and t.get("type") == "page" and t.get("webSocketDebuggerUrl")),
                None,
            )
            if page_tab:
                page_ws = str(page_tab["webSocketDebuggerUrl"])
            if browser_ws and page_ws:
                return browser_ws, page_ws
        except Exception:
            continue
    return browser_ws, page_ws


async def _close_browser(browser_ws: str | None, proc: subprocess.Popen[bytes] | None) -> None:
    """优先通过 CDP 关闭整个浏览器；进程句柄只作为兜底。"""
    if browser_ws:
        try:
            async with AsyncSession() as session:
                ws = await session.ws_connect(browser_ws)
                await ws.send_json({"id": 1, "method": "Browser.close"})
                await ws.close()
        except Exception:
            pass
    if proc is not None and proc.poll() is None:
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

    port = _find_free_port()
    temp_dir = tempfile.mkdtemp(prefix="wk_browser_")

    cmd = [
        browser_bin,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={temp_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-extensions",
        "about:blank",
    ]
    proc = subprocess.Popen(cmd)
    browser_ws: str | None = None

    try:
        browser_ws, page_ws = await _wait_debugger(port)
        if not page_ws:
            raise RiskControlError("无法连接到浏览器调试端口", web_url=web_url)

        _notify(
            "security.captcha_challenge",
            {
                "web_url": web_url,
                "browser": browser_bin,
                "timeout": timeout,
            },
        )

        async with AsyncSession() as session:
            ws = await session.ws_connect(page_ws)
            cdp = _CdpClient(ws)
            try:
                if cookies:
                    payload = [{"name": k, "value": v, "domain": cookie_domain, "path": "/"} for k, v in cookies.items()]
                    await cdp.call("Network.setCookies", {"cookies": payload})

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
    finally:
        await _close_browser(browser_ws, proc)
        shutil.rmtree(temp_dir, ignore_errors=True)
