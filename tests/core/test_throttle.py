"""节流、退避与限流提示解析的回归测试。"""

import time
from typing import Any

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.domain.errors import APIError
from wkhelper.infra.http import HTTPError
from wkhelper.infra.throttle import RateGate, RetryPolicy, parse_retry_hint
from wkhelper.platform.base import BasePlatform


class _Resp:
    def __init__(self, status: int = 200, body: str = "{}", headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.text = body
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return {"ok": True}


class _Session:
    """按脚本顺序返回响应并记录调用次数。"""

    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}

    async def request(self, method: str, url: str, **kwargs: Any) -> _Resp:
        self.calls += 1
        return self._responses.pop(0)


def _platform(session: _Session, policy: RetryPolicy | None = None) -> BasePlatform:
    return FakeAsyncPlatform(session, retry_policy=policy or RetryPolicy(max_attempts=3, base_backoff=0.01, jitter_ratio=0))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def test_parse_retry_hint_from_header_and_body() -> None:
    """优先使用 Retry-After 头，其次解析响应体中的等待提示。"""
    assert parse_retry_hint("", "12") == 12.0
    assert parse_retry_hint("Expected available in 07 second.") == 7.0
    assert parse_retry_hint("请等待 3 秒后重试") == 3.0
    assert parse_retry_hint("no hint") is None


@pytest.mark.asyncio
async def test_gate_cooldown_blocks_subsequent_requests() -> None:
    """平台限流后冷却期内所有请求都应被推迟。"""
    gate = RateGate(min_interval=0.0, jitter_ratio=0.0)
    await gate.cooldown(0.12)
    start = time.perf_counter()
    await gate.acquire()
    assert time.perf_counter() - start >= 0.1


@pytest.mark.asyncio
async def test_transient_5xx_is_retried() -> None:
    """5xx 属于可重试错误，应在退避后再次请求。"""
    session = _Session([_Resp(503), _Resp(200)])
    result = await _platform(session)._request_json("GET", "https://api.test/x")
    assert result == {"ok": True}
    assert session.calls == 2


@pytest.mark.asyncio
async def test_client_error_is_not_retried() -> None:
    """4xx（非 401/403/429）不应重试，避免放大错误。"""
    session = _Session([_Resp(400), _Resp(200)])
    with pytest.raises(APIError) as excinfo:
        await _platform(session)._request_json("GET", "https://api.test/x")
    assert session.calls == 1
    assert excinfo.value.retryable is False


@pytest.mark.asyncio
async def test_rate_limit_hint_triggers_retry_and_cooldown() -> None:
    """200 响应携带限流提示时按提示等待并重试，同时设置全局冷却。"""
    session = _Session([_Resp(200, "Expected available in 0 second."), _Resp(200)])
    platform = _platform(session)
    result = await platform._request_json("GET", "https://api.test/x")
    assert result == {"ok": True}
    assert session.calls == 2


@pytest.mark.asyncio
async def test_429_exhausts_attempts_with_platform_error() -> None:
    """持续 429 时最终抛出可重试的平台错误，而不是内部错误。"""
    session = _Session([_Resp(429, "", {"Retry-After": "0"}) for _ in range(3)])
    with pytest.raises(APIError) as excinfo:
        await _platform(session)._request_json("GET", "https://api.test/x")
    assert excinfo.value.retryable is True
    assert session.calls == 3
