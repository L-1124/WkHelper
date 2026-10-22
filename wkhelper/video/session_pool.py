"""视频截帧 Warm Session 进程池与空闲超时 Watchdog。

通过跨命令常驻 Chromium 实例与文件锁状态协调，避免重复启动浏览器与重复加载播放器，
并将连续截帧耗时从秒级降低至毫秒级。
"""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import AsyncGenerator, Generator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from wkhelper.infra.browser.cdp import CdpClient
from wkhelper.infra.browser.process import BrowserManager
from wkhelper.infra.http import build_session

logger = logging.getLogger(__name__)


@dataclass
class VideoWarmSession:
    """热池视频会话元数据。"""

    course_id: str
    video_id: str
    pid: int
    port: int
    profile_dir: str
    page_ws: str | None = None
    browser_ws: str | None = None
    last_active_at: float = field(default_factory=time.time)
    ttl_seconds: float = 120.0
    web_url: str = ""
    watchdog_pid: int | None = None
    account_id: str | None = None
    platform: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VideoWarmSession":
        return cls(
            course_id=str(data.get("course_id", "")),
            video_id=str(data.get("video_id", "")),
            pid=int(data.get("pid", 0)),
            port=int(data.get("port", 0)),
            profile_dir=str(data.get("profile_dir", "")),
            page_ws=data.get("page_ws"),
            browser_ws=data.get("browser_ws"),
            last_active_at=float(data.get("last_active_at", time.time())),
            ttl_seconds=float(data.get("ttl_seconds", 120.0)),
            web_url=str(data.get("web_url", "")),
            watchdog_pid=int(data["watchdog_pid"]) if data.get("watchdog_pid") is not None else None,
            account_id=str(data["account_id"]) if data.get("account_id") is not None else None,
            platform=str(data["platform"]) if data.get("platform") is not None else None,
        )


def get_default_video_session_dir(base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        p = Path(base_dir) / "video_sessions"
    else:
        from wkhelper.infra.credentials import get_default_data_dir

        p = get_default_data_dir() / "video_sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_session_file(
    course_id: str,
    video_id: str,
    session_dir: Path | None = None,
    account_id: str | None = None,
    platform: str | None = None,
) -> Path:
    safe_cid = re.sub(r"[^\w\-.]", "_", str(course_id))
    safe_vid = re.sub(r"[^\w\-.]", "_", str(video_id))
    parts = []
    if platform:
        parts.append(re.sub(r"[^\w\-.]", "_", str(platform)))
    if account_id:
        parts.append(re.sub(r"[^\w\-.]", "_", str(account_id)))
    parts.extend([safe_cid, safe_vid])
    file_name = f"session_{'_'.join(parts)}.json"
    target_dir = session_dir or get_default_video_session_dir()
    return target_dir / file_name


_FILE_LOCK_OWNERS: dict[str, tuple[int, int | None, int]] = {}
_FILE_LOCK_MUTEX = threading.Lock()


@contextlib.asynccontextmanager
async def async_file_lock(
    lock_path: Path | str,
    timeout: float = 15.0,
    poll_interval: float = 0.02,
) -> AsyncGenerator[None, None]:
    """复用同步锁的非阻塞尝试，等待时让出事件循环，取消时不遗留持锁线程。"""
    deadline = time.monotonic() + timeout
    while True:
        with contextlib.ExitStack() as stack:
            try:
                stack.enter_context(file_lock(lock_path, timeout=0))
            except TimeoutError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"获取会话文件锁超时 ({timeout}s): {lock_path}") from None
            else:
                yield
                return
        await asyncio.sleep(min(max(0.001, poll_interval), remaining))


@contextlib.contextmanager
def file_lock(lock_path: Path | str, timeout: float = 15.0) -> Generator[None, None, None]:
    """跨进程与跨线程的文件互斥锁（阻塞等待）。"""
    path_str = str(lock_path)
    actual_lock = Path(path_str if path_str.endswith(".lock") else path_str + ".lock")
    actual_lock.parent.mkdir(parents=True, exist_ok=True)
    norm_path = str(actual_lock.resolve())
    thread_id = threading.get_ident()
    try:
        running_loop = asyncio.get_running_loop()
        current_task = asyncio.current_task(running_loop)
        task_id = id(current_task) if current_task is not None else None
    except RuntimeError:
        task_id = None

    with _FILE_LOCK_MUTEX:
        owner = _FILE_LOCK_OWNERS.get(norm_path)
        if owner is not None and owner[0] == thread_id and owner[1] == task_id:
            _FILE_LOCK_OWNERS[norm_path] = (thread_id, task_id, owner[2] + 1)
            is_reentrant = True
        else:
            is_reentrant = False

    if is_reentrant:
        try:
            yield
        finally:
            with _FILE_LOCK_MUTEX:
                owner = _FILE_LOCK_OWNERS.get(norm_path)
                if owner is not None:
                    if owner[2] > 1:
                        _FILE_LOCK_OWNERS[norm_path] = (thread_id, task_id, owner[2] - 1)
                    else:
                        _FILE_LOCK_OWNERS.pop(norm_path, None)
        return

    start_time = time.monotonic()
    with open(actual_lock, "a+b") as init_f:
        if init_f.tell() == 0:
            init_f.write(b"\x00")
            init_f.flush()

    fd = open(actual_lock, "r+b")
    acquired = False
    try:
        while True:
            can_try_os = False
            with _FILE_LOCK_MUTEX:
                if norm_path not in _FILE_LOCK_OWNERS:
                    can_try_os = True

            if can_try_os:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        fd.seek(0)
                        msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

                    with _FILE_LOCK_MUTEX:
                        if norm_path not in _FILE_LOCK_OWNERS:
                            _FILE_LOCK_OWNERS[norm_path] = (thread_id, task_id, 1)
                            acquired = True
                            break
                        else:
                            if sys.platform == "win32":
                                import msvcrt

                                fd.seek(0)
                                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                            else:
                                import fcntl

                                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass

            if time.monotonic() - start_time >= timeout:
                raise TimeoutError(f"获取会话文件锁超时 ({timeout}s): {actual_lock}")
            time.sleep(0.02)

        yield
    finally:
        if acquired:
            with _FILE_LOCK_MUTEX:
                _FILE_LOCK_OWNERS.pop(norm_path, None)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    fd.seek(0)
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        try:
            fd.close()
        except Exception:
            pass


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        process_query_limited_info = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_info, False, pid)
        if not handle:
            return False
        exit_code = wintypes.DWORD()
        success = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(success and exit_code.value == still_active)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def check_cdp_alive(port: int, timeout: float = 1.0) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return isinstance(data, dict) and "Browser" in data
    except Exception:
        return False


def get_active_page_ws(port: int, default_ws: str | None = None) -> str | None:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
            if isinstance(targets, list):
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return str(t["webSocketDebuggerUrl"])
    except Exception:
        pass
    return default_ws


def terminate_process_tree(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
            )
        except Exception:
            pass
    else:
        try:
            pgid = os.getpgid(pid)
            if pgid != os.getpgrp():
                os.killpg(pgid, 15)
                time.sleep(0.1)
                os.killpg(pgid, 9)
            else:
                os.kill(pid, 15)
                time.sleep(0.1)
                os.kill(pid, 9)
        except Exception:
            try:
                os.kill(pid, 9)
            except Exception:
                pass


def clean_session_resources(session: VideoWarmSession, meta_file: Path | str | None = None) -> None:
    if session.pid > 0 and is_pid_alive(session.pid):
        terminate_process_tree(session.pid)
    if session.watchdog_pid and session.watchdog_pid != os.getpid() and is_pid_alive(session.watchdog_pid):
        terminate_process_tree(session.watchdog_pid)
    if session.profile_dir and os.path.exists(session.profile_dir):
        shutil.rmtree(session.profile_dir, ignore_errors=True)
    if meta_file:
        meta_p = Path(meta_file)
        if meta_p.exists():
            try:
                meta_p.unlink()
            except OSError:
                pass


def touch_session(session_file: Path | str) -> None:
    path = Path(session_file)
    if not path.exists():
        return
    try:
        with file_lock(path, timeout=3.0):
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                data["last_active_at"] = time.time()
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug(f"更新会话活跃时间戳失败: {exc}")


def _try_http_close(port: int) -> None:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
            if isinstance(targets, list):
                for target in targets:
                    target_id = target.get("id")
                    if target_id:
                        close_req = urllib.request.Request(f"http://127.0.0.1:{port}/json/close/{target_id}")
                        try:
                            urllib.request.urlopen(close_req, timeout=0.5)
                        except Exception:
                            pass
    except Exception:
        pass


def _cleanup_meta_and_profile(meta_path: Path, profile_path: Path | None) -> None:
    if meta_path.exists():
        try:
            meta_path.unlink()
        except OSError:
            pass
    if profile_path and profile_path.exists():
        shutil.rmtree(profile_path, ignore_errors=True)


def run_watchdog(
    port: int,
    pid: int,
    profile_dir: str,
    meta_file: str,
    ttl_seconds: float = 120.0,
    check_interval: float = 2.0,
    max_ticks: int | None = None,
) -> None:
    meta_path = Path(meta_file)
    profile_path = Path(profile_dir) if profile_dir else None
    ticks = 0
    consecutive_corrupted = 0
    while True:
        if max_ticks is not None and ticks >= max_ticks:
            break
        ticks += 1
        time.sleep(check_interval)

        if not is_pid_alive(pid):
            _cleanup_meta_and_profile(meta_path, profile_path)
            break

        if not meta_path.exists():
            terminate_process_tree(pid)
            if profile_path and profile_path.exists():
                shutil.rmtree(profile_path, ignore_errors=True)
            break

        try:
            with file_lock(meta_path, timeout=1.0):
                if not meta_path.exists():
                    terminate_process_tree(pid)
                    if profile_path and profile_path.exists():
                        shutil.rmtree(profile_path, ignore_errors=True)
                    break
                try:
                    content = meta_path.read_text(encoding="utf-8")
                    data = json.loads(content)
                    consecutive_corrupted = 0
                    last_active_at = float(data.get("last_active_at", 0.0))
                    session_ttl = float(data.get("ttl_seconds", ttl_seconds))
                except Exception:
                    consecutive_corrupted += 1
                    if consecutive_corrupted >= 3:
                        logger.warning(f"会话元数据损坏，正在清理会话资源: {meta_path}")
                        terminate_process_tree(pid)
                        _cleanup_meta_and_profile(meta_path, profile_path)
                        break
                    continue

                idle_duration = time.time() - last_active_at
                if idle_duration >= session_ttl:
                    _try_http_close(port)
                    terminate_process_tree(pid)
                    _cleanup_meta_and_profile(meta_path, profile_path)
                    break
        except Exception:
            continue


def spawn_watchdog(
    port: int,
    pid: int,
    profile_dir: str,
    meta_file: str,
    ttl_seconds: float = 120.0,
    check_interval: float = 2.0,
) -> int | None:
    cmd = [
        sys.executable,
        "-m",
        "wkhelper.video.session_pool",
        "--watchdog",
        "--port",
        str(port),
        "--pid",
        str(pid),
        "--profile-dir",
        str(profile_dir),
        "--meta-file",
        str(meta_file),
        "--ttl",
        str(ttl_seconds),
        "--check-interval",
        str(check_interval),
    ]
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True
        kwargs["close_fds"] = True
    try:
        proc = subprocess.Popen(cmd, **kwargs)
        return proc.pid
    except Exception as exc:
        logger.warning(f"启动 Watchdog 守护进程失败: {exc}")
        return None


async def get_or_create_warm_session(
    course_id: str,
    video_id: str,
    web_url: str,
    cookies: dict[str, str],
    cookie_domain: str,
    session_dir: Path | None = None,
    ttl_seconds: float = 120.0,
    timeout: float = 15.0,
    account_id: str | None = None,
    platform: str | None = None,
) -> tuple[VideoWarmSession, bool]:
    session_file = get_session_file(
        course_id=course_id,
        video_id=video_id,
        session_dir=session_dir,
        account_id=account_id,
        platform=platform,
    )
    target_dir = session_file.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    async with async_file_lock(session_file, timeout=10.0):
        if session_file.exists():
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                existing = VideoWarmSession.from_dict(data)
                if is_pid_alive(existing.pid) and check_cdp_alive(existing.port):
                    existing.last_active_at = time.time()
                    existing.web_url = web_url or existing.web_url
                    session_file.write_text(json.dumps(existing.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
                    return existing, False
                else:
                    clean_session_resources(existing, session_file)
            except Exception:
                try:
                    session_file.unlink()
                except OSError:
                    pass

        b_proc = BrowserManager.launch_browser(mode="headless", detached=True)
        try:
            browser_ws, page_ws = await BrowserManager.wait_debugger(b_proc.port, timeout=min(8.0, timeout))
            if not page_ws:
                raise RuntimeError(f"无法连接到浏览器调试端口 127.0.0.1:{b_proc.port}")

            if cookies:
                async with build_session(timeout=15.0, proxy=None, user_agent=None, impersonate=None) as http_sess:
                    ws = await http_sess.ws_connect(page_ws)
                    cdp = CdpClient(ws)
                    try:
                        payload = [{"name": k, "value": v, "domain": cookie_domain or "", "path": "/"} for k, v in cookies.items()]
                        await cdp.call("Network.setCookies", {"cookies": payload})
                    finally:
                        await cdp.close()

            watchdog_pid = spawn_watchdog(
                port=b_proc.port,
                pid=b_proc.pid,
                profile_dir=b_proc.profile_dir,
                meta_file=str(session_file),
                ttl_seconds=ttl_seconds,
            )

            new_session = VideoWarmSession(
                course_id=str(course_id),
                video_id=str(video_id),
                pid=b_proc.pid,
                port=b_proc.port,
                profile_dir=b_proc.profile_dir,
                page_ws=page_ws,
                browser_ws=browser_ws,
                last_active_at=time.time(),
                ttl_seconds=ttl_seconds,
                web_url=web_url,
                watchdog_pid=watchdog_pid,
                account_id=account_id,
                platform=platform,
            )
            session_file.write_text(json.dumps(new_session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            return new_session, True
        except BaseException:
            clean_session_resources(
                VideoWarmSession(
                    course_id=str(course_id),
                    video_id=str(video_id),
                    pid=b_proc.pid,
                    port=b_proc.port,
                    profile_dir=b_proc.profile_dir,
                ),
                session_file,
            )
            raise


async def acquire_warm_session(
    course_id: str,
    video_id: str,
    web_url: str,
    cookies: dict[str, str],
    cookie_domain: str,
    session_dir: Path | None = None,
    ttl_seconds: float = 120.0,
    timeout: float = 15.0,
    account_id: str | None = None,
    platform: str | None = None,
) -> VideoWarmSession:
    sess, _ = await get_or_create_warm_session(
        course_id=course_id,
        video_id=video_id,
        web_url=web_url,
        cookies=cookies,
        cookie_domain=cookie_domain,
        session_dir=session_dir,
        ttl_seconds=ttl_seconds,
        timeout=timeout,
        account_id=account_id,
        platform=platform,
    )
    return sess


def close_warm_session(
    session: VideoWarmSession,
    session_dir: Path | None = None,
    account_id: str | None = None,
    platform: str | None = None,
) -> None:
    resolved_account = account_id if account_id is not None else session.account_id
    resolved_platform = platform if platform is not None else session.platform
    session_file = get_session_file(
        session.course_id,
        session.video_id,
        session_dir=session_dir,
        account_id=resolved_account,
        platform=resolved_platform,
    )
    with file_lock(session_file, timeout=5.0):
        clean_session_resources(session, session_file)


def cleanup_all_warm_sessions(session_dir: Path | None = None) -> int:
    target_dir = session_dir or get_default_video_session_dir()
    if not target_dir.exists():
        return 0
    cleaned = 0
    for file_path in list(target_dir.glob("session_*.json")):
        try:
            with file_lock(file_path, timeout=3.0):
                if not file_path.exists():
                    continue
                try:
                    data = json.loads(file_path.read_text(encoding="utf-8"))
                    sess = VideoWarmSession.from_dict(data)
                    clean_session_resources(sess, file_path)
                    cleaned += 1
                except Exception as exc:
                    logger.debug(f"会话元数据损坏，清理无效文件 {file_path}: {exc}")
                    try:
                        file_path.unlink()
                    except OSError:
                        pass
        except TimeoutError:
            logger.debug(f"获取会话文件锁超时，会话可能正在活跃使用，跳过清理: {file_path}")
            continue
        except Exception as exc:
            logger.debug(f"清理会话文件 {file_path} 异常: {exc}")
    return cleaned


def _watchdog_main() -> None:
    parser = argparse.ArgumentParser(description="Watchdog for wkhelper warm browser session")
    parser.add_argument("--watchdog", action="store_true", help="Run in watchdog mode")
    parser.add_argument("--port", type=int, required=True, help="Browser CDP port")
    parser.add_argument("--pid", type=int, required=True, help="Browser PID")
    parser.add_argument("--profile-dir", type=str, required=True, help="Browser profile directory")
    parser.add_argument("--meta-file", type=str, required=True, help="Session metadata file path")
    parser.add_argument("--ttl", type=float, default=120.0, help="Idle TTL in seconds")
    parser.add_argument("--check-interval", type=float, default=2.0, help="Check interval in seconds")
    args = parser.parse_args()

    run_watchdog(
        port=args.port,
        pid=args.pid,
        profile_dir=args.profile_dir,
        meta_file=args.meta_file,
        ttl_seconds=args.ttl,
        check_interval=args.check_interval,
    )


if __name__ == "__main__":
    _watchdog_main()

__all__ = [
    "VideoWarmSession",
    "acquire_warm_session",
    "check_cdp_alive",
    "clean_session_resources",
    "cleanup_all_warm_sessions",
    "close_warm_session",
    "async_file_lock",
    "file_lock",
    "get_active_page_ws",
    "get_default_video_session_dir",
    "get_session_file",
    "get_or_create_warm_session",
    "is_pid_alive",
    "run_watchdog",
    "spawn_watchdog",
    "terminate_process_tree",
    "touch_session",
]
