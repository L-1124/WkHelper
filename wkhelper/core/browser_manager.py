"""Chromium / Edge 浏览器统一生命周期管理与 CDP 桥接。

提供进程拉起、端口与临时 Profile 隔离、连接就绪探测、活跃池全局跟踪与 atexit 回收守护。
"""

import asyncio
import atexit
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class BrowserProcess:
    """受管 Chromium 浏览器进程实例描述。"""

    pid: int
    port: int
    profile_dir: str
    mode: Literal["headless", "headful"]
    proc: subprocess.Popen[Any]
    created_at: float
    browser_ws: str | None = None
    page_ws: str | None = None

    def __hash__(self) -> int:
        return hash((self.pid, self.port, self.profile_dir))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BrowserProcess):
            return False
        return (self.pid, self.port, self.profile_dir) == (other.pid, other.port, other.profile_dir)


class CdpClient:
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
        except Exception as exc:
            # 遍历并中断所有尚未得到响应的 pending Future
            err = exc if isinstance(exc, Exception) else RuntimeError("CDP WebSocket 连接断开")
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(err)
            self._pending.clear()
        finally:
            self.alive = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("CDP 客户端连接已关闭"))
            self._pending.clear()

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any]:
        if not self.alive:
            raise RuntimeError("CDP 客户端已关闭或断开连接")
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send_json({"id": req_id, "method": method, "params": params or {}})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"CDP 方法调用超时 ({timeout}s): {method}") from None

    async def close(self) -> None:
        self._pump_task.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass


_CdpClient = CdpClient


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_chromium_browser() -> str | None:
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


def _http_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=1) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _wait_debugger(port: int, timeout: float = 15.0) -> tuple[str | None, str | None]:
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


_ACTIVE_PROCESSES: set[BrowserProcess] = set()
_LOCK = threading.Lock()


def _terminate_and_clean(p: BrowserProcess, timeout: float = 3.0) -> None:
    if p.proc.poll() is None:
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2.0,
                )
            except Exception:
                pass
            try:
                p.proc.terminate()
            except Exception:
                pass
        else:
            try:
                pgid = os.getpgid(p.pid)
                if pgid != os.getpgrp():
                    os.killpg(pgid, 15)  # SIGTERM
                else:
                    p.proc.terminate()
            except Exception:
                try:
                    p.proc.terminate()
                except Exception:
                    pass

        try:
            p.proc.wait(timeout=timeout)
        except Exception:
            if sys.platform != "win32":
                try:
                    pgid = os.getpgid(p.pid)
                    if pgid != os.getpgrp():
                        os.killpg(pgid, 9)  # SIGKILL
                except Exception:
                    pass
            try:
                p.proc.kill()
            except Exception:
                pass
    if p.profile_dir and os.path.exists(p.profile_dir):
        shutil.rmtree(p.profile_dir, ignore_errors=True)


def _cleanup_all() -> None:
    with _LOCK:
        procs = list(_ACTIVE_PROCESSES)
        _ACTIVE_PROCESSES.clear()
    for p in procs:
        try:
            _terminate_and_clean(p, timeout=1.0)
        except Exception:
            pass


atexit.register(_cleanup_all)


class BrowserManager:
    """Chromium / Edge 浏览器统一生命周期管理者。"""

    @classmethod
    def launch_browser(
        cls,
        mode: Literal["headless", "headful"] = "headless",
        extra_args: list[str] | None = None,
        detached: bool = False,
    ) -> BrowserProcess:
        browser_bin = find_chromium_browser()
        if not browser_bin:
            raise RuntimeError("未检测到本地 Chromium 浏览器（Chrome/Edge）")

        port = _find_free_port()
        profile_dir = tempfile.mkdtemp(prefix="wk_browser_")

        cmd = [
            browser_bin,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-extensions",
        ]
        if mode == "headless":
            cmd.extend(
                [
                    "--headless=new",
                    "--disable-gpu",
                    "--mute-audio",
                    "--window-size=1280,720",
                ]
            )
        if extra_args:
            cmd.extend(extra_args)
        cmd.append("about:blank")

        kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if detached:
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
        elif sys.platform != "win32":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **kwargs)
        record = BrowserProcess(
            pid=proc.pid,
            port=port,
            profile_dir=profile_dir,
            mode=mode,
            proc=proc,
            created_at=time.time(),
        )

        if not detached:
            with _LOCK:
                _ACTIVE_PROCESSES.add(record)

        return record

    @classmethod
    def close_browser(cls, proc: BrowserProcess, timeout: float = 3.0) -> None:
        with _LOCK:
            _ACTIVE_PROCESSES.discard(proc)
        _terminate_and_clean(proc, timeout=timeout)

    @classmethod
    async def close_browser_async(cls, proc: BrowserProcess, timeout: float = 3.0) -> None:
        await asyncio.to_thread(cls.close_browser, proc, timeout)

    @classmethod
    async def _wait_debugger(cls, port: int, timeout: float = 8.0) -> tuple[str | None, str | None]:
        return await _wait_debugger(port, timeout=timeout)

    @classmethod
    @asynccontextmanager
    async def acquire_session(
        cls,
        mode: Literal["headless", "headful"] = "headless",
        cookies: dict[str, str] | None = None,
        cookie_domain: str | None = None,
        extra_args: list[str] | None = None,
        timeout: float = 8.0,
    ) -> AsyncIterator[BrowserProcess]:
        b_proc = cls.launch_browser(mode=mode, extra_args=extra_args, detached=False)
        try:
            browser_ws, page_ws = await _wait_debugger(b_proc.port, timeout=timeout)
            if not page_ws:
                raise RuntimeError(f"无法连接到浏览器调试端口 127.0.0.1:{b_proc.port}")
            b_proc.browser_ws = browser_ws
            b_proc.page_ws = page_ws

            if cookies:
                async with AsyncSession() as session:
                    ws = await session.ws_connect(page_ws)
                    cdp = CdpClient(ws)
                    try:
                        payload = [{"name": k, "value": v, "domain": cookie_domain or "", "path": "/"} for k, v in cookies.items()]
                        await cdp.call("Network.setCookies", {"cookies": payload})
                    finally:
                        await cdp.close()

            yield b_proc
        finally:
            cls.close_browser(b_proc)


launch_browser = BrowserManager.launch_browser
close_browser = BrowserManager.close_browser
close_browser_async = BrowserManager.close_browser_async
acquire_session = BrowserManager.acquire_session

__all__ = [
    "BrowserManager",
    "BrowserProcess",
    "CdpClient",
    "_CdpClient",
    "_cleanup_all",
    "_find_free_port",
    "_wait_debugger",
    "acquire_session",
    "close_browser",
    "close_browser_async",
    "find_chromium_browser",
    "launch_browser",
]
