"""服务器时钟对齐测试：Date 头、server_time 与心跳时间戳。"""

import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.domain.time_types import parse_http_date_ms


class _Resp:
    def __init__(self, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> None:
        self.status_code = 200
        self.text = "{}"
        self.headers = headers or {}
        self._payload = payload or {"ok": True}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _Session:
    def __init__(self, response: _Resp) -> None:
        self._response = response
        self.cookies = {}
        self.headers = {}

    async def request(self, method: str, url: str, **kwargs: Any) -> _Resp:
        return self._response


def _http_date(seconds_from_now: float) -> str:
    return format_datetime(datetime.now(UTC) + timedelta(seconds=seconds_from_now), usegmt=True)


def test_parse_http_date_ms_handles_gmt_and_invalid() -> None:
    parsed = parse_http_date_ms("Mon, 19 Oct 2026 12:52:15 GMT")
    assert parsed == 1792414335000
    assert parse_http_date_ms("not-a-date") is None
    assert parse_http_date_ms(None) is None


@pytest.mark.asyncio
async def test_request_syncs_clock_from_date_header() -> None:
    """成功响应后按 Date 头记录本机与服务器的偏移。"""
    platform = FakeAsyncPlatform(_Session(_Resp(headers={"date": _http_date(5)})))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    await platform._request_json("GET", "https://api.test/ping")
    assert 4000 <= platform.clock_offset_ms <= 6500


@pytest.mark.asyncio
async def test_payload_server_time_wins_over_date_header() -> None:
    """响应体 server_time 精度高于 Date 头，应优先采用。"""
    server_ms = int((time.time() + 10) * 1000)
    platform = FakeAsyncPlatform(_Session(_Resp(payload={"server_time": server_ms})))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    await platform._request_json("GET", "https://api.test/ping")
    assert 9000 <= platform.clock_offset_ms <= 11000


@pytest.mark.asyncio
async def test_heartbeat_ts_uses_server_clock() -> None:
    """心跳 ts 必须叠加已测量的时钟偏移。"""
    platform = FakeAsyncPlatform(_Session(_Resp()))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    platform.clock.offset_ms = 5000.0
    payload = platform.build_heartbeat_payload(
        video_id="v1",
        classroom_id="c1",
        user_id=1,
        course_id=2,
        sku_id=3,
        video_frame=12.5,
        seq=1,
        timestamp=1_000_000,
        cc="cc1",
        duration=60.0,
    )
    assert payload["ts"] == "1005000"
