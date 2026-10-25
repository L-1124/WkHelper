"""浏览器认证状态与资源回收回归测试。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wkhelper.auth import browser
from wkhelper.domain.accounts import UserInfo
from wkhelper.domain.errors import APIError, AuthError
from wkhelper.platform.icourse163 import Icourse163Platform

pytestmark = pytest.mark.asyncio


@pytest.fixture
def login_browser(monkeypatch, fake_platform):
    state = SimpleNamespace(browser_closed=False, session_closed=False, cookie_batches=[], calls=[])
    process = SimpleNamespace(proc=SimpleNamespace(poll=lambda: None, wait=Mock(return_value=0)), page_ws="ws://localhost/page")
    cdp = SimpleNamespace(alive=True, close=AsyncMock())

    async def call(method, params=None, **kwargs):
        state.calls.append((method, params))
        if method == "Network.getCookies":
            batch = state.cookie_batches.pop(0) if state.cookie_batches else {}
            return {"cookies": [{"name": name, "value": value} for name, value in batch.items()]}
        return {}

    cdp.call = call

    @asynccontextmanager
    async def acquire_session(**kwargs):
        state.mode = kwargs["mode"]
        try:
            yield process
        finally:
            state.browser_closed = True

    @asynccontextmanager
    async def build_session(**kwargs):
        assert kwargs["proxy"] is None
        assert kwargs["impersonate"] is None
        try:
            yield SimpleNamespace(ws_connect=AsyncMock(return_value=object()))
        finally:
            state.session_closed = True

    monkeypatch.setattr(browser.BrowserManager, "acquire_session", acquire_session)
    monkeypatch.setattr(browser, "build_session", build_session)
    monkeypatch.setattr(browser, "CdpClient", lambda ws: cdp)
    monkeypatch.setattr(fake_platform, "login_url", "https://login.example/account")
    monkeypatch.setattr(fake_platform, "essential_cookies", ("session", "csrf"))
    state.platform = fake_platform
    state.process = process
    state.cdp = cdp
    return state


def assert_released(state):
    assert state.browser_closed
    assert state.session_closed
    state.cdp.close.assert_awaited_once()


async def test_browser_validates_complete_cookies_and_persists(login_browser):
    state = login_browser
    user = UserInfo(id="42", name="登录用户")
    state.cookie_batches = [
        {"session": "anonymous"},
        {"session": "anonymous", "csrf": "token"},
        {"session": "authenticated", "csrf": "token", "extra": "retained"},
    ]
    state.platform.login_with_cookies = AsyncMock(side_effect=[AuthError("尚未登录"), user])
    result = await browser.login_with_browser(state.platform, timeout=5)
    assert result == user
    assert state.platform.current_cookies == {"session": "authenticated", "csrf": "token", "extra": "retained"}
    assert state.platform.login_with_cookies.await_count == 2
    assert ("Page.navigate", {"url": state.platform.login_url}) in state.calls
    assert ("Network.getCookies", {"urls": [state.platform.login_url]}) in state.calls
    assert state.mode == "headful"
    assert_released(state)


@pytest.mark.parametrize("closed", ["process", "connection"])
async def test_browser_closed_cancels_login(login_browser, closed):
    state = login_browser
    if closed == "process":
        state.process.proc.poll = lambda: 0
    else:
        state.cdp.alive = False
    with pytest.raises(AuthError, match="登录取消"):
        await browser.login_with_browser(state.platform)
    assert_released(state)


@pytest.mark.parametrize("cookies", [{}, {"session": "anonymous", "csrf": "token"}])
async def test_browser_timeout_releases_resources(login_browser, cookies):
    state = login_browser
    state.cookie_batches = [cookies]
    state.platform.login_with_cookies = AsyncMock(side_effect=AuthError("尚未登录"))
    with pytest.raises(AuthError, match="登录超时"):
        await browser.login_with_browser(state.platform, timeout=1, mode="headless")
    assert state.mode == "headless"
    assert_released(state)


async def test_browser_platform_failure_propagates(login_browser):
    state = login_browser
    state.cookie_batches = [{"session": "authenticated", "csrf": "token"}]
    state.platform.login_with_cookies = AsyncMock(side_effect=APIError("服务不可用"))
    with pytest.raises(APIError, match="服务不可用"):
        await browser.login_with_browser(state.platform)
    assert_released(state)


async def test_mooc_browser_accepts_actual_session_cookie(login_browser):
    state = login_browser
    platform = Icourse163Platform(state.platform.client)
    user = UserInfo(id="42", name="MOOC 用户")
    state.cookie_batches = [{"STUDY_SESS": "authenticated", "NTESSTUDYSI": "csrf"}]
    platform.login_with_cookies = AsyncMock(return_value=user)
    assert await browser.login_with_browser(platform, timeout=1) == user
    assert platform.current_cookies == {"STUDY_SESS": "authenticated", "NTESSTUDYSI": "csrf"}
    assert_released(state)


async def test_browser_cancellation_releases_resources(login_browser):
    state = login_browser
    waiting = asyncio.Event()

    async def blocked_call(method, params=None, **kwargs):
        if method == "Browser.close":
            return {}
        waiting.set()
        await asyncio.Future()

    state.cdp.call = blocked_call
    task = asyncio.create_task(browser.login_with_browser(state.platform))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_released(state)
