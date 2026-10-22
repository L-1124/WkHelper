"""扫码登录模板与心跳参数隔离的回归测试。"""

import json
from typing import Any, override

import pytest

from wkhelper.auth import service as auth_service
from wkhelper.domain.accounts import UserInfo
from wkhelper.domain.courses import Course
from wkhelper.infra.http import AsyncSession
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.protocol_types import HeartbeatProfile
from wkhelper.video.engine import VideoWatchOptions, generic_watch_video


class _FakeSocket:
    """模拟 curl_cffi WebSocket：按序吐出报文并记录已发送载荷。"""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = [json.dumps(m) for m in messages]
        self.sent: list[str] = []
        self.closed = False

    async def send_str(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv_str(self) -> str:
        return self._messages.pop(0) if self._messages else ""

    async def __aenter__(self) -> "_FakeSocket":
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.closed = True


class _FakeWsSession:
    """模拟带 ws_connect 的 curl_cffi 会话。"""

    def __init__(self, socket: _FakeSocket) -> None:
        self._socket = socket

    def ws_connect(self, url: str, **kwargs: Any) -> _FakeSocket:
        return self._socket

    async def __aenter__(self) -> "_FakeWsSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class _QrPlatform(BasePlatform):
    """只实现协议钩子的最小平台，用于验证基类扫码模板。"""

    published: list[dict[str, Any]] = []

    @property
    @override
    def heartbeat_profile(self) -> HeartbeatProfile:
        return HeartbeatProfile(lob="test")

    @override
    def qr_ws_url(self) -> str:
        return "wss://login.test/wsapp/"

    @override
    def qr_request_payload(self) -> dict[str, Any]:
        return {"op": "requestlogin"}

    @override
    def qr_expire_seconds(self, message: dict[str, Any]) -> float | None:
        raw = message.get("expire_seconds")
        return float(raw) if raw is not None else None

    @override
    async def qr_publish_image(self, message: dict[str, Any], qr_session: Any, expires_in: float) -> None:
        self.published.append(message)

    @override
    async def qr_exchange_cookies(self, login_data: dict[str, Any]) -> dict[str, str]:
        self.receipt = login_data
        return {"csrftoken": "csrf", "sessionid": "sid"}

    @override
    async def login_with_cookies(self, cookies: dict[str, str]) -> UserInfo:
        assert cookies == {"csrftoken": "csrf", "sessionid": "sid"}
        self.user = UserInfo(id="42", name="测试用户")
        return self.user

    @override
    async def get_courses(self) -> list[Course]:
        return []

    @override
    async def get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        return []

    @override
    async def get_leaf_schedules(self, course: Course) -> dict[int, float]:
        return {}

    @override
    def _build_submit_payload(
        self,
        problem_id: int,
        answer: list[str],
        submit_ctx: dict[str, Any] | None = None,
        question: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {}

    @override
    async def prepare_video_context(self, video_id: str, course: Course):  # type: ignore[override]
        raise NotImplementedError

    @override
    async def prepare_submit_context(self, homework: Any, course: Course) -> dict[str, Any]:
        return {}


@pytest.mark.asyncio
async def test_qr_login_template_drives_hooks(monkeypatch) -> None:
    """基类扫码模板应按序推送二维码、应用超时提示并完成凭据换取。"""
    socket = _FakeSocket(
        [
            {"qrcode": "payload", "expire_seconds": 45},
            {"op": "loginsuccess", "token": "tk-123"},
        ]
    )
    monkeypatch.setattr(auth_service, "build_session", lambda **kwargs: _FakeWsSession(socket))

    async with AsyncSession() as client:
        platform = _QrPlatform(client)
        platform.published = []
        platform.receipt = {}
        user = await platform.login(cookies=None, timeout=5, display="image")

    assert user.name == "测试用户"
    assert platform.receipt["token"] == "tk-123"
    assert socket.sent == ['{"op": "requestlogin"}']
    assert [m.get("qrcode") or "loginsuccess" for m in platform.published] == ["payload", "loginsuccess"]
    assert socket.closed is True


class _RecordingClient:
    """记录 HTTP 调用参数的假客户端。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("GET", url, kwargs))
        return _ProgressResponse()

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("POST", url, kwargs))
        return _ProgressResponse()


class _ProgressResponse:
    text = '{"data": {"v1": {"rate": 0.1, "watch_length": 5}}}'
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {}


def _heartbeat_payload(
    video_id: str,
    classroom_id: str,
    user_id: int,
    course_id: int,
    sku_id: int,
    video_frame: float,
    seq: int,
    timestamp: int,
    cc: str,
    duration: float,
) -> dict[str, Any]:
    return {"sq": seq, "ts": timestamp}


@pytest.mark.asyncio
async def test_heartbeat_request_excludes_progress_params() -> None:
    """平台进度 query 参数只能出现在进度查询上，心跳 POST 不得携带。"""
    client = _RecordingClient()
    await generic_watch_video(
        client=client,  # type: ignore[arg-type]
        video_id="v1",
        video_name="测试",
        classroom_id="9",
        user_id=1,
        course_id=2,
        sku_id=3,
        progress_url="https://x/progress",
        heartbeat_url="https://x/heartbeat",
        payload_gen=_heartbeat_payload,
        progress_params={"video_id": "v1", "vtype": "rate"},
        request_kwargs={"headers": {"classroom-id": "9"}},
        options=VideoWatchOptions(video_timeout=0.6, stall_timeout=0.3, completion_threshold=2.0, max_heartbeats=1),
    )

    gets = [c for c in client.calls if c[0] == "GET"]
    posts = [c for c in client.calls if c[0] == "POST"]
    assert gets and posts
    assert all(c[2]["params"] == {"video_id": "v1", "vtype": "rate"} for c in gets)
    assert all("params" not in c[2] for c in posts)
    assert all(c[2]["headers"] == {"classroom-id": "9"} for c in client.calls)
