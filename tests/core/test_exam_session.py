"""考场浏览器会话生命周期、异常清理与交接测试。"""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.domain.accounts import UserInfo
from wkhelper.domain.errors import APIError
from wkhelper.exam.service import bind_browser_session
from wkhelper.exam.session import ExamBrowserSession, load_session, open_exam_browser, save_session, session_file
from wkhelper.infra.browser.process import BrowserManager, BrowserProcess
from wkhelper.infra.http import build_session


def _make_fake_proc(tmp_path: Path, port: int = 9222) -> BrowserProcess:
    profile = tmp_path / f"profile_{port}"
    profile.mkdir(parents=True, exist_ok=True)
    mock_popen = MagicMock()
    mock_popen.poll.return_value = 0
    return BrowserProcess(
        pid=12345,
        port=port,
        profile_dir=str(profile),
        mode="headful",
        proc=mock_popen,
        created_at=1000.0,
    )


@pytest.mark.asyncio
async def test_open_browser_reclaims_on_debugger_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """调试端口未就绪时，必须回收进程并清理 profile，不得残留 detached 孤儿进程。"""
    fake_proc = _make_fake_proc(tmp_path)
    monkeypatch.setattr("wkhelper.exam.session.find_chromium_browser", lambda: "fake_chromium")
    monkeypatch.setattr("wkhelper.exam.session.load_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(BrowserManager, "launch_browser", lambda **kwargs: fake_proc)

    closed: list[BrowserProcess] = []
    monkeypatch.setattr(BrowserManager, "close_browser", lambda proc: closed.append(proc))

    async def fake_wait_dbg(port: int, timeout: float = 8.0) -> tuple[str | None, str | None]:
        del port, timeout
        return None, None

    monkeypatch.setattr("wkhelper.exam.session.wait_debugger", fake_wait_dbg)

    with pytest.raises(APIError, match="调试端口未就绪"):
        await open_exam_browser(
            account_id="1",
            exam_id="3001",
            platform="xtzx",
            host="https://examination.test",
            eid=42,
            web_url="https://examination.test/exam/42",
            cookies={},
            data_dir=tmp_path,
        )

    assert fake_proc in closed


@pytest.mark.asyncio
async def test_open_browser_reclaims_on_ws_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CDP 连接建立失败时，统一回收进程与 profile。"""
    fake_proc = _make_fake_proc(tmp_path)
    monkeypatch.setattr("wkhelper.exam.session.find_chromium_browser", lambda: "fake_chromium")
    monkeypatch.setattr("wkhelper.exam.session.load_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(BrowserManager, "launch_browser", lambda **kwargs: fake_proc)

    closed: list[BrowserProcess] = []
    monkeypatch.setattr(BrowserManager, "close_browser", lambda proc: closed.append(proc))

    async def fake_wait_dbg(port: int, timeout: float = 8.0) -> tuple[str | None, str | None]:
        del port, timeout
        return "ws://127.0.0.1:9222/devtools/browser", "ws://127.0.0.1:9222/devtools/page/1"

    monkeypatch.setattr("wkhelper.exam.session.wait_debugger", fake_wait_dbg)

    class _MockAsyncSession:
        async def __aenter__(self) -> "_MockAsyncSession":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def ws_connect(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionRefusedError("WS refused")

    monkeypatch.setattr("wkhelper.exam.session.build_session", lambda *a, **kw: _MockAsyncSession())

    with pytest.raises(ConnectionRefusedError):
        await open_exam_browser(
            account_id="1",
            exam_id="3001",
            platform="xtzx",
            host="https://examination.test",
            eid=42,
            web_url="https://examination.test/exam/42",
            cookies={},
            data_dir=tmp_path,
        )

    assert fake_proc in closed


@pytest.mark.asyncio
async def test_open_browser_reclaims_on_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """初始化过程中被外部取消（asyncio.CancelledError）时，必须回收进程与 profile。"""
    fake_proc = _make_fake_proc(tmp_path)
    monkeypatch.setattr("wkhelper.exam.session.find_chromium_browser", lambda: "fake_chromium")
    monkeypatch.setattr("wkhelper.exam.session.load_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(BrowserManager, "launch_browser", lambda **kwargs: fake_proc)

    closed: list[BrowserProcess] = []
    monkeypatch.setattr(BrowserManager, "close_browser", lambda proc: closed.append(proc))

    async def _mock_cancelled(port: int, timeout: float = 8.0) -> tuple[str | None, str | None]:
        del port, timeout
        raise asyncio.CancelledError()

    monkeypatch.setattr("wkhelper.exam.session.wait_debugger", _mock_cancelled)

    with pytest.raises(asyncio.CancelledError):
        await open_exam_browser(
            account_id="1",
            exam_id="3001",
            platform="xtzx",
            host="https://examination.test",
            eid=42,
            web_url="https://examination.test/exam/42",
            cookies={},
            data_dir=tmp_path,
        )

    assert fake_proc in closed


@pytest.mark.asyncio
async def test_open_browser_reclaims_on_save_session_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """会话文件持久化失败时，不得留下无管理记录的常驻进程，必须立即清理。"""
    fake_proc = _make_fake_proc(tmp_path)
    monkeypatch.setattr("wkhelper.exam.session.find_chromium_browser", lambda: "fake_chromium")
    monkeypatch.setattr("wkhelper.exam.session.load_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(BrowserManager, "launch_browser", lambda **kwargs: fake_proc)

    closed: list[BrowserProcess] = []
    monkeypatch.setattr(BrowserManager, "close_browser", lambda proc: closed.append(proc))

    async def fake_wait_dbg(port: int, timeout: float = 8.0) -> tuple[str | None, str | None]:
        del port, timeout
        return "ws://127.0.0.1:9222/devtools/browser", "ws://127.0.0.1:9222/devtools/page/1"

    monkeypatch.setattr("wkhelper.exam.session.wait_debugger", fake_wait_dbg)

    mock_ws = AsyncMock()
    mock_ws.recv.return_value = '{"id": 1, "result": {}}'
    mock_ws.send.return_value = None
    mock_ws.close = AsyncMock()

    class _MockAsyncSession:
        async def __aenter__(self) -> "_MockAsyncSession":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def ws_connect(self, *args: Any, **kwargs: Any) -> Any:
            return mock_ws

    monkeypatch.setattr("wkhelper.exam.session.build_session", lambda *a, **kw: _MockAsyncSession())
    cdp = MagicMock(call=AsyncMock(return_value={}), close=AsyncMock())
    monkeypatch.setattr("wkhelper.exam.session.CdpClient", lambda ws: cdp)

    def _failing_save(record: ExamBrowserSession, data_dir: Any = None) -> Path:
        del record, data_dir
        raise OSError("Disk full")

    monkeypatch.setattr("wkhelper.exam.session.save_session", _failing_save)

    with pytest.raises(OSError, match="Disk full"):
        await open_exam_browser(
            account_id="1",
            exam_id="3001",
            platform="xtzx",
            host="https://examination.test",
            eid=42,
            web_url="https://examination.test/exam/42",
            cookies={"sessionid": "s123"},
            data_dir=tmp_path,
        )

    assert fake_proc in closed


@pytest.mark.asyncio
async def test_open_browser_successful_handover_keeps_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """成功持久化后交接会话，不杀常驻浏览器进程。"""
    fake_proc = _make_fake_proc(tmp_path)
    monkeypatch.setattr("wkhelper.exam.session.find_chromium_browser", lambda: "fake_chromium")
    monkeypatch.setattr("wkhelper.exam.session.load_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(BrowserManager, "launch_browser", lambda **kwargs: fake_proc)

    closed: list[BrowserProcess] = []
    monkeypatch.setattr(BrowserManager, "close_browser", lambda proc: closed.append(proc))

    async def fake_wait_dbg(port: int, timeout: float = 8.0) -> tuple[str | None, str | None]:
        del port, timeout
        return "ws://127.0.0.1:9222/devtools/browser", "ws://127.0.0.1:9222/devtools/page/1"

    monkeypatch.setattr("wkhelper.exam.session.wait_debugger", fake_wait_dbg)

    mock_ws = AsyncMock()
    mock_ws.recv.return_value = '{"id": 1, "result": {}}'
    mock_ws.send.return_value = None
    mock_ws.close = AsyncMock()

    class _MockAsyncSession:
        async def __aenter__(self) -> "_MockAsyncSession":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def ws_connect(self, *args: Any, **kwargs: Any) -> Any:
            return mock_ws

    monkeypatch.setattr("wkhelper.exam.session.build_session", lambda *a, **kw: _MockAsyncSession())
    cdp = MagicMock(call=AsyncMock(return_value={}), close=AsyncMock())
    monkeypatch.setattr("wkhelper.exam.session.CdpClient", lambda ws: cdp)

    session = await open_exam_browser(
        account_id="1",
        exam_id="3001",
        platform="xtzx",
        host="https://examination.test",
        eid=42,
        web_url="https://examination.test/exam/42",
        cookies={"sessionid": "s123"},
        data_dir=tmp_path,
    )

    assert session.exam_id == "3001"
    assert session.port == fake_proc.port
    assert session.profile == fake_proc.profile_dir
    # 成功交接：进程未被关闭，常驻等待复用
    assert fake_proc not in closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_platform", "session_account", "session_exam", "matches"),
    [
        ("xtzx", "1", "3001", True),
        ("ykt", "1", "3001", False),
        ("xtzx", "2", "3001", False),
        ("xtzx", "1", "3002", False),
    ],
)
async def test_browser_session_reuse_is_scoped_to_identity_and_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session_platform: str,
    session_account: str,
    session_exam: str,
    matches: bool,
) -> None:
    selected_dir = tmp_path / "selected"
    default_dir = tmp_path / "default"
    record = ExamBrowserSession(
        platform=session_platform,
        account_id=session_account,
        exam_id=session_exam,
        host="https://exam.test",
        eid=42,
        port=1,
        profile="",
        web_url="https://exam.test/exam/42",
    )
    save_session(record, selected_dir)
    save_session(
        ExamBrowserSession(
            platform="xtzx",
            account_id="1",
            exam_id="3001",
            host="https://other.test",
            eid=99,
            port=1,
            profile="",
            web_url="https://other.test/exam/99",
        ),
        default_dir,
    )
    monkeypatch.setattr("wkhelper.exam.session.get_default_data_dir", lambda: default_dir)
    monkeypatch.setattr("wkhelper.exam.session._port_alive", lambda port: True)

    async def read_browser_cookies(session: ExamBrowserSession) -> dict[str, str]:
        return {"sessionid": "browser-account"}

    monkeypatch.setattr("wkhelper.exam.service.browser_cookies", read_browser_cookies)
    async with build_session(proxy=None, user_agent=None, impersonate=None) as client:
        platform = FakeAsyncPlatform(client)
        platform.platform_code = "xtzx"
        platform.user = UserInfo(id="1", name="Current")
        client.cookies.set("sessionid", "current-account")
        reused = await bind_browser_session(platform, "3001", data_dir=selected_dir)
        assert reused == (record if matches else None)
        assert client.cookies.get("sessionid") == ("browser-account" if matches else "current-account")


def test_browser_session_without_owner_is_rejected(tmp_path: Path) -> None:
    record = ExamBrowserSession(
        platform="xtzx",
        account_id="1",
        exam_id="3001",
        host="https://exam.test",
        eid=42,
        port=1,
        profile="",
        web_url="https://exam.test/exam/42",
    )
    session_file(tmp_path).write_text(record.model_dump_json(exclude={"account_id"}), encoding="utf-8")
    assert load_session(tmp_path, verify=False) is None
