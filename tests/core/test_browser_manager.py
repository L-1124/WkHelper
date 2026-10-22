"""浏览器生命周期管理与 CDP 客户端单元测试。"""

import asyncio
import os
import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wkhelper.infra.browser.cdp import CdpClient
from wkhelper.infra.browser.process import (
    _ACTIVE_PROCESSES,
    BrowserManager,
    BrowserProcess,
    _cleanup_all,
    find_chromium_browser,
    find_free_port,
)


def test_find_chromium_browser() -> None:
    path = find_chromium_browser()
    assert path is None or isinstance(path, str)


def test_find_chromium_browser_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    with patch("os.path.isfile", side_effect=lambda p: p == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        assert find_chromium_browser() == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def test_find_chromium_browser_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    with (
        patch("shutil.which", side_effect=lambda name: "/usr/bin/google-chrome" if name == "google-chrome" else None),
        patch("os.path.isfile", return_value=True),
    ):
        assert find_chromium_browser() == "/usr/bin/google-chrome"


def test_find_free_port() -> None:
    p1 = find_free_port()
    p2 = find_free_port()
    assert isinstance(p1, int) and p1 > 0
    assert isinstance(p2, int) and p2 > 0


def test_browser_process_equality_and_hash() -> None:
    mock_proc = MagicMock(spec=subprocess.Popen)
    b1 = BrowserProcess(pid=100, port=9222, profile_dir="/tmp/p1", mode="headless", proc=mock_proc, created_at=1.0)
    b2 = BrowserProcess(pid=100, port=9222, profile_dir="/tmp/p1", mode="headless", proc=mock_proc, created_at=2.0)
    b3 = BrowserProcess(pid=101, port=9223, profile_dir="/tmp/p2", mode="headless", proc=mock_proc, created_at=1.0)

    assert b1 == b2
    assert hash(b1) == hash(b2)
    assert b1 != b3
    assert b1 != object()


def test_launch_browser_no_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("wkhelper.infra.browser.process.find_chromium_browser", lambda: None)
    with pytest.raises(RuntimeError, match="未检测到本地 Chromium 浏览器"):
        BrowserManager.launch_browser()


def test_launch_and_close_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = tempfile.mkdtemp(prefix="test_bm_")
    monkeypatch.setattr("wkhelper.infra.browser.process.find_chromium_browser", lambda: "/usr/bin/chrome")
    monkeypatch.setattr("tempfile.mkdtemp", lambda **kw: temp_dir)

    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.pid = 9999
    mock_proc.poll.return_value = None
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: mock_proc)

    b_proc = BrowserManager.launch_browser(mode="headless", extra_args=["--test-flag"])
    assert b_proc in _ACTIVE_PROCESSES
    assert b_proc.pid == 9999
    assert b_proc.mode == "headless"
    assert os.path.exists(temp_dir)

    BrowserManager.close_browser(b_proc)
    assert b_proc not in _ACTIVE_PROCESSES
    mock_proc.terminate.assert_called_once()
    assert not os.path.exists(temp_dir)


def test_launch_detached_not_tracked(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = tempfile.mkdtemp(prefix="test_detached_")
    monkeypatch.setattr("wkhelper.infra.browser.process.find_chromium_browser", lambda: "/usr/bin/chrome")
    monkeypatch.setattr("tempfile.mkdtemp", lambda **kw: temp_dir)

    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.pid = 8888
    mock_proc.poll.return_value = None
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: mock_proc)

    b_proc = BrowserManager.launch_browser(mode="headful", detached=True)
    assert b_proc not in _ACTIVE_PROCESSES

    BrowserManager.close_browser(b_proc)
    mock_proc.terminate.assert_called_once()


def test_cleanup_all() -> None:
    temp_dir = tempfile.mkdtemp(prefix="test_cleanup_")
    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.pid = 7777
    mock_proc.poll.return_value = None

    b_proc = BrowserProcess(pid=7777, port=9999, profile_dir=temp_dir, mode="headless", proc=mock_proc, created_at=1.0)
    _ACTIVE_PROCESSES.add(b_proc)

    _cleanup_all()
    assert len(_ACTIVE_PROCESSES) == 0
    mock_proc.terminate.assert_called_once()
    assert not os.path.exists(temp_dir)


@pytest.mark.asyncio
async def test_acquire_session_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = tempfile.mkdtemp(prefix="test_sess_")
    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.pid = 6666
    mock_proc.poll.return_value = None

    monkeypatch.setattr("wkhelper.infra.browser.process.find_chromium_browser", lambda: "/usr/bin/chrome")
    monkeypatch.setattr("tempfile.mkdtemp", lambda **kw: temp_dir)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: mock_proc)
    monkeypatch.setattr(
        "wkhelper.infra.browser.process.wait_debugger",
        AsyncMock(return_value=("ws://127.0.0.1:9222/browser", "ws://127.0.0.1:9222/page")),
    )

    async with BrowserManager.acquire_session(mode="headless") as b_proc:
        assert b_proc.page_ws == "ws://127.0.0.1:9222/page"
        assert b_proc.browser_ws == "ws://127.0.0.1:9222/browser"
        assert b_proc in _ACTIVE_PROCESSES

    assert b_proc not in _ACTIVE_PROCESSES
    mock_proc.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_cdp_client_call_and_events() -> None:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    mock_ws = AsyncMock()
    mock_ws.recv_json.side_effect = queue.get

    client = CdpClient(mock_ws)

    await queue.put({"id": 1, "result": {"value": 123}})
    await queue.put({"method": "Page.loadEventFired", "params": {}})

    res = await client.call("Test.method", {"arg": "val"})
    assert res == {"value": 123}
    mock_ws.send_json.assert_called_with({"id": 1, "method": "Test.method", "params": {"arg": "val"}})

    await asyncio.sleep(0.01)
    assert len(client.events) == 1
    assert client.events[0]["method"] == "Page.loadEventFired"

    await client.close()


@pytest.mark.asyncio
async def test_cdp_client_pending_fail_on_disconnect() -> None:
    mock_ws = AsyncMock()
    # 模拟连接突然断开抛出异常
    mock_ws.recv_json.side_effect = ConnectionResetError("Connection lost")

    client = CdpClient(mock_ws)

    with pytest.raises(Exception, match="Connection lost|连接"):
        await client.call("Page.enable", timeout=2.0)

    assert not client.alive
    await client.close()


@pytest.mark.asyncio
async def test_cdp_client_timeout() -> None:
    mock_ws = AsyncMock()
    # 模拟永远收不到响应
    mock_ws.recv_json.side_effect = asyncio.Event().wait

    client = CdpClient(mock_ws)
    with pytest.raises(TimeoutError, match="CDP 方法调用超时"):
        await client.call("Page.navigate", timeout=0.05)

    await client.close()
