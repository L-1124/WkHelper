"""命令行调试模式测试：验证错误堆栈诊断与 NDJSON 结构化事件流。"""

import json
import logging
from io import StringIO

import pytest

from wkhelper.infra.events import is_debug_enabled, set_debug_enabled
from wkhelper.infra.http import AsyncSession
from wkhelper.main import async_main
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.protocol_types import HeartbeatProfile


@pytest.mark.asyncio
async def test_debug_mode_injects_traceback_into_error_envelope(monkeypatch):
    """验证开启 --debug 时，错误信封的 details 字段注入 traceback 与 exception_type。"""
    stdout_buf = StringIO()
    monkeypatch.setattr("sys.stdout", stdout_buf)

    code = await async_main(["--debug", "--credential-mode", "session", "course", "inspect", "-c", "nonexistent"])
    assert code != 0

    envelope = json.loads(stdout_buf.getvalue().strip())
    assert envelope["ok"] is False
    assert "error" in envelope
    details = envelope["error"].get("details", {})
    assert "traceback" in details
    assert isinstance(details["traceback"], list)
    assert len(details["traceback"]) > 0
    assert "exception_type" in details


@pytest.mark.asyncio
async def test_normal_mode_does_not_leak_traceback(monkeypatch):
    """验证未开启 --debug 时，错误信封 details 不泄露内部堆栈。"""
    stdout_buf = StringIO()
    monkeypatch.setattr("sys.stdout", stdout_buf)

    code = await async_main(["--credential-mode", "session", "course", "inspect", "-c", "nonexistent"])
    assert code != 0

    envelope = json.loads(stdout_buf.getvalue().strip())
    assert envelope["ok"] is False
    details = envelope["error"].get("details", {})
    assert "traceback" not in details


def test_ndjson_log_handler_bridges_logger_debug(monkeypatch):
    """验证 --debug 激活后 logger.debug 自动转为 stderr NDJSON debug.log 事件。"""
    stderr_buf = StringIO()
    monkeypatch.setattr("sys.stderr", stderr_buf)

    try:
        set_debug_enabled(True)
        assert is_debug_enabled() is True

        test_logger = logging.getLogger("wkhelper.test_module")
        test_logger.debug("这是一条测试调试日志")

        output = stderr_buf.getvalue().strip()
        assert output != ""
        event = json.loads(output)
        assert event["event"] == "debug.log"
        assert event["payload"]["message"] == "这是一条测试调试日志"
        assert event["payload"]["level"] == "DEBUG"
    finally:
        set_debug_enabled(False)


@pytest.mark.asyncio
async def test_http_trace_emits_ndjson_events(monkeypatch):
    """验证开启调试时，平台底层 HTTP 请求自动派发 http.trace 事件。"""
    stderr_buf = StringIO()
    monkeypatch.setattr("sys.stderr", stderr_buf)

    class DummyPlatform(BasePlatform):
        @property
        def heartbeat_profile(self) -> HeartbeatProfile:
            return HeartbeatProfile(lob="test")

        def _build_submit_url(self) -> str:
            return ""

        def _build_submit_payload(self, problem_id: int, answer: list[str], submit_ctx=None, question=None):
            return {}

        async def prepare_submit_context(self, homework, course):
            return {}

        async def prepare_video_context(self, video_id: str, course):
            raise NotImplementedError

        async def login_with_cookies(self, cookies):
            raise NotImplementedError

        async def qr_exchange_cookies(self, login_data):

            return {}

        async def get_courses(self):
            return []

        async def get_chapter_data(self, course):
            return []

        async def get_leaf_schedules(self, course):
            return {}

        def _build_leaf_info_url(self, leaf_id, course):
            return ""

        def _get_leaf_info_params(self, leaf_id, course):
            return {}

        def _build_exercise_list_url(self, leaf_type_id, sku_id=None):
            return ""

    try:
        set_debug_enabled(True)
        async with AsyncSession() as client:
            plat = DummyPlatform(client)

            # 模拟假响应
            class FakeResp:
                status_code = 200
                text = ""
                headers: dict[str, str] = {}

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"success": True}

            async def fake_request(*args, **kwargs):
                return FakeResp()

            monkeypatch.setattr(client, "request", fake_request)

            res = await plat._request_json("GET", "https://api.test/v1/mock")
            assert res == {"success": True}

        output = stderr_buf.getvalue().strip()
        lines = [json.loads(line) for line in output.splitlines() if line.strip()]
        trace_events = [ev for ev in lines if ev.get("event") == "http.trace"]
        assert len(trace_events) == 1
        assert trace_events[0]["payload"]["method"] == "GET"
        assert trace_events[0]["payload"]["url"] == "https://api.test/v1/mock"
        assert trace_events[0]["payload"]["status_code"] == 200
        assert "duration_ms" in trace_events[0]["payload"]
    finally:
        set_debug_enabled(False)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cookie", "plan"])
async def test_debug_validation_does_not_disclose_input_values(kind, tmp_path, monkeypatch) -> None:
    secret = "redaction-regression-sentinel"
    path = tmp_path / "input.json"
    if kind == "cookie":
        payload = {"sessionid": [secret]}
        args = ["auth", "login", "-p", "ykt", "-m", "cookie", "--cookie-file", str(path)]
    else:
        payload = {"targets": [{"course_id": [secret], "video_id": "v"}]}
        args = ["video", "learn", "--plan-file", str(path), "--dry-run"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    stdout, stderr = StringIO(), StringIO()
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)
    code = await async_main(["--debug", "--credential-mode", "session", *args])
    assert code == 2
    envelope = json.loads(stdout.getvalue())
    assert envelope["error"]["code"] == "INVALID_ARGUMENT"
    assert secret not in stdout.getvalue() + stderr.getvalue()
    assert "input_value" not in stdout.getvalue()
