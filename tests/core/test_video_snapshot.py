"""视频纯净抽帧参数校验、时间戳计算与核心流程单元测试。"""

import asyncio
import base64
import contextlib
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wkhelper.domain.video import VideoSnapshotData, VideoSnapshotFrame
from wkhelper.infra.browser.process import BrowserProcess
from wkhelper.video.snapshot import _extract_frames_cdp, capture_video_frames


def test_video_snapshot_models() -> None:
    frame = VideoSnapshotFrame(
        timestamp=45.5,
        file_path="/tmp/f.jpg",
        data_url="data:image/jpeg;base64,abc",
        width=1280,
        height=720,
        size_bytes=1024,
    )
    assert frame.timestamp == 45.5
    assert frame.file_path == "/tmp/f.jpg"
    assert frame.width == 1280
    assert frame.error is None

    summary = VideoSnapshotData(
        course_id="c1",
        video_id="v1",
        total_frames=1,
        frames=[frame],
    )
    assert summary.total_frames == 1
    assert summary.frames[0].timestamp == 45.5


@pytest.mark.asyncio
async def test_capture_video_frames_explicit_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = tempfile.mkdtemp(prefix="test_snap_")
    out_dir = Path(temp_dir)

    mock_b_proc = BrowserProcess(
        pid=1111,
        port=9222,
        profile_dir=temp_dir,
        mode="headless",
        proc=AsyncMock(),
        created_at=1.0,
        browser_ws="ws://browser",
        page_ws="ws://page",
    )

    class MockContextManager:
        async def __aenter__(self) -> BrowserProcess:
            return mock_b_proc

        async def __aexit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(
        "wkhelper.infra.browser.process.BrowserManager.acquire_session",
        lambda *a, **kw: MockContextManager(),
    )

    dummy_jpeg = b"\xff\xd8\xff\xe0testjpeg"
    dummy_b64 = base64.b64encode(dummy_jpeg).decode("ascii")

    class MockWs:
        async def send_json(self, data: dict[str, object]) -> None:
            pass

        async def close(self) -> None:
            pass

    mock_cdp_call = AsyncMock()

    async def fake_call(method: str, params: dict[str, object] | None = None, **kw: object) -> dict[str, object]:
        if method == "Runtime.evaluate":
            expr = str((params or {}).get("expression", ""))
            if "duration" in expr and "hasDuration" in expr:
                return {
                    "result": {
                        "value": {
                            "found": True,
                            "ready": True,
                            "duration": 120.0,
                            "videoWidth": 1280,
                            "videoHeight": 720,
                        }
                    }
                }
            if "canvas" in expr:
                return {
                    "result": {
                        "value": {
                            "ok": True,
                            "dataUrl": f"data:image/jpeg;base64,{dummy_b64}",
                            "width": 1280,
                            "height": 720,
                        }
                    }
                }
        return {"result": {}}

    mock_cdp_call.side_effect = fake_call
    with (
        patch("curl_cffi.requests.AsyncSession.ws_connect", new=AsyncMock(return_value=MockWs())),
        patch("wkhelper.video.snapshot.CdpClient.call", side_effect=fake_call),
    ):
        frames = await capture_video_frames(
            web_url="https://test.com/video/1",
            cookies={"session": "123"},
            cookie_domain=".test.com",
            timestamps=[30.0, 60.0, 150.0],  # 150.0 should clamp to duration 120.0
            output_dir=out_dir,
        )

    assert len(frames) == 3
    assert frames[0].timestamp == 30.0
    assert frames[1].timestamp == 60.0
    assert frames[2].timestamp == 120.0  # clamped to 120
    assert frames[0].file_path is not None
    assert Path(frames[0].file_path).exists()
    assert frames[0].size_bytes == len(dummy_jpeg)


@pytest.mark.asyncio
async def test_capture_video_frames_step_and_range(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = tempfile.mkdtemp(prefix="test_snap_step_")
    out_dir = Path(temp_dir)

    mock_b_proc = BrowserProcess(
        pid=2222,
        port=9222,
        profile_dir=temp_dir,
        mode="headless",
        proc=AsyncMock(),
        created_at=1.0,
        browser_ws="ws://browser",
        page_ws="ws://page",
    )

    class MockContextManager:
        async def __aenter__(self) -> BrowserProcess:
            return mock_b_proc

        async def __aexit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(
        "wkhelper.infra.browser.process.BrowserManager.acquire_session",
        lambda *a, **kw: MockContextManager(),
    )

    dummy_jpeg = b"\xff\xd8\xff\xe0testjpeg"
    dummy_b64 = base64.b64encode(dummy_jpeg).decode("ascii")

    class MockWs:
        async def send_json(self, data: dict[str, object]) -> None:
            pass

        async def close(self) -> None:
            pass

    async def fake_call(method: str, params: dict[str, object] | None = None, **kw: object) -> dict[str, object]:
        if method == "Runtime.evaluate":
            expr = str((params or {}).get("expression", ""))
            if "duration" in expr and "hasDuration" in expr:
                return {
                    "result": {
                        "value": {
                            "found": True,
                            "ready": True,
                            "duration": 100.0,
                            "videoWidth": 1280,
                            "videoHeight": 720,
                        }
                    }
                }
            if "canvas" in expr:
                return {
                    "result": {
                        "value": {
                            "ok": True,
                            "dataUrl": f"data:image/jpeg;base64,{dummy_b64}",
                            "width": 1280,
                            "height": 720,
                        }
                    }
                }
        return {"result": {}}

    with (
        patch("curl_cffi.requests.AsyncSession.ws_connect", new=AsyncMock(return_value=MockWs())),
        patch("wkhelper.video.snapshot.CdpClient.call", side_effect=fake_call),
    ):
        frames = await capture_video_frames(
            web_url="https://test.com/video/1",
            cookies={},
            cookie_domain=".test.com",
            start=10.0,
            end=40.0,
            step=10.0,
            output_dir=out_dir,
        )

    assert len(frames) == 4
    assert [f.timestamp for f in frames] == [10.0, 20.0, 30.0, 40.0]


@pytest.mark.asyncio
async def test_capture_video_frames_canvas_fallback_to_screenshot(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = tempfile.mkdtemp(prefix="test_fallback_")
    out_dir = Path(temp_dir)

    mock_b_proc = BrowserProcess(
        pid=3333,
        port=9222,
        profile_dir=temp_dir,
        mode="headless",
        proc=AsyncMock(),
        created_at=1.0,
        browser_ws="ws://browser",
        page_ws="ws://page",
    )

    class MockContextManager:
        async def __aenter__(self) -> BrowserProcess:
            return mock_b_proc

        async def __aexit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(
        "wkhelper.infra.browser.process.BrowserManager.acquire_session",
        lambda *a, **kw: MockContextManager(),
    )

    fallback_jpeg = b"\xff\xd8\xff\xe0fallback"
    fallback_b64 = base64.b64encode(fallback_jpeg).decode("ascii")

    class MockWs:
        async def send_json(self, data: dict[str, object]) -> None:
            pass

        async def close(self) -> None:
            pass

    async def fake_call(method: str, params: dict[str, object] | None = None, **kw: object) -> dict[str, object]:
        if method == "Runtime.evaluate":
            expr = str((params or {}).get("expression", ""))
            if "duration" in expr and "hasDuration" in expr:
                return {
                    "result": {
                        "value": {
                            "found": True,
                            "ready": True,
                            "duration": 50.0,
                            "videoWidth": 1280,
                            "videoHeight": 720,
                        }
                    }
                }
            if "canvas" in expr:
                # Simulates SecurityError
                return {
                    "result": {
                        "value": {
                            "ok": False,
                            "fallback_required": True,
                            "error": "SecurityError: The operation is insecure.",
                        }
                    }
                }
            if "selectors" in expr:
                # hide DOM returns rect
                return {"result": {"value": {"x": 0, "y": 0, "width": 1280, "height": 720}}}
        if method == "Page.captureScreenshot":
            return {"data": fallback_b64}
        return {"result": {}}

    with (
        patch("curl_cffi.requests.AsyncSession.ws_connect", new=AsyncMock(return_value=MockWs())),
        patch("wkhelper.video.snapshot.CdpClient.call", side_effect=fake_call),
    ):
        frames = await capture_video_frames(
            web_url="https://test.com/video/1",
            cookies={},
            cookie_domain=".test.com",
            timestamps=[15.0],
            output_dir=out_dir,
        )

    assert len(frames) == 1
    assert frames[0].timestamp == 15.0
    assert frames[0].file_path is not None
    assert Path(frames[0].file_path).exists()
    assert Path(frames[0].file_path).read_bytes() == fallback_jpeg
    assert frames[0].error is None


@pytest.mark.asyncio
async def test_video_snapshot_cmd_validation() -> None:
    from wkhelper.cli.commands.video import video_snapshot_cmd

    with pytest.raises(ValueError, match="必须指定抽帧模式"):
        await video_snapshot_cmd(course="1001", video="2001")

    with pytest.raises(ValueError, match="必须指定抽帧模式"):
        await video_snapshot_cmd(course="1001", video="2001", start=10.0, end=20.0)
    with pytest.raises(ValueError, match="时间戳格式错误"):
        await video_snapshot_cmd(course="1001", video="2001", timestamps="invalid,abc")

    with pytest.raises(ValueError, match="未能解析出有效时间戳"):
        await video_snapshot_cmd(course="1001", video="2001", timestamps="   ")

    with pytest.raises(ValueError, match="采样步长 step 必须大于 0"):
        await video_snapshot_cmd(course="1001", video="2001", step=0)

    with pytest.raises(ValueError, match="采样起始时间 start 不能小于 0"):
        await video_snapshot_cmd(course="1001", video="2001", start=-1, step=10)

    with pytest.raises(ValueError, match="采样结束时间 end 不能小于 0"):
        await video_snapshot_cmd(course="1001", video="2001", end=-5, step=10)
    with pytest.raises(ValueError, match="不能大于结束时间"):
        await video_snapshot_cmd(course="1001", video="2001", start=100, end=50, step=10)

    with pytest.raises(ValueError, match="均匀抽帧数量 count 必须大于 0"):
        await video_snapshot_cmd(course="1001", video="2001", count=0)


def test_resolve_timestamps_validation_and_clamping() -> None:
    from wkhelper.video.snapshot import _resolve_timestamps

    # 正常分段
    ts = _resolve_timestamps(duration=100.0, start=10.0, end=30.0, step=10.0)
    assert ts == [10.0, 20.0, 30.0]

    # count = 1
    ts_single = _resolve_timestamps(duration=100.0, count=1)
    assert ts_single == [50.0]

    # duration 无效
    with pytest.raises(ValueError, match="视频时长无效"):
        _resolve_timestamps(duration=0.0, timestamps=[10.0])

    # 异常范围
    with pytest.raises(ValueError, match="step 必须大于 0"):
        _resolve_timestamps(duration=100.0, step=-1.0)

    with pytest.raises(ValueError, match="start 不能小于 0"):
        _resolve_timestamps(duration=100.0, start=-5.0)

    with pytest.raises(ValueError, match="不能大于结束时间"):
        _resolve_timestamps(duration=100.0, start=50.0, end=20.0, step=5.0)

    with pytest.raises(ValueError, match="必须指定 `timestamps`、`step` 或 `count` 采样模式"):
        _resolve_timestamps(duration=100.0, start=10.0, end=20.0)

    # 超出 duration 自动钳位
    clamped = _resolve_timestamps(duration=60.0, timestamps=[10.0, 70.0, -5.0])
    assert clamped == [10.0, 60.0, 0.0]


@pytest.mark.asyncio
async def test_capture_video_frames_seek_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = tempfile.mkdtemp(prefix="test_seek_timeout_")
    out_dir = Path(temp_dir)

    mock_b_proc = BrowserProcess(
        pid=4444,
        port=9222,
        profile_dir=temp_dir,
        mode="headless",
        proc=AsyncMock(),
        created_at=1.0,
        browser_ws="ws://browser",
        page_ws="ws://page",
    )

    class MockContextManager:
        async def __aenter__(self) -> BrowserProcess:
            return mock_b_proc

        async def __aexit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(
        "wkhelper.infra.browser.process.BrowserManager.acquire_session",
        lambda *a, **kw: MockContextManager(),
    )

    class MockWs:
        async def send_json(self, data: dict[str, object]) -> None:
            pass

        async def close(self) -> None:
            pass

    async def fake_call(method: str, params: dict[str, object] | None = None, **kw: object) -> dict[str, object]:
        if method == "Runtime.evaluate":
            expr = str((params or {}).get("expression", ""))
            if "duration" in expr and "hasDuration" in expr:
                return {
                    "result": {
                        "value": {
                            "found": True,
                            "ready": True,
                            "duration": 50.0,
                            "videoWidth": 1280,
                            "videoHeight": 720,
                        }
                    }
                }
            if "targetTime" in expr:
                # 模拟 seek 超时返回
                return {
                    "result": {
                        "value": {
                            "ok": False,
                            "error": "seek_timeout",
                            "actualTime": 0.0,
                        }
                    }
                }
        return {"result": {}}

    with (
        patch("curl_cffi.requests.AsyncSession.ws_connect", new=AsyncMock(return_value=MockWs())),
        patch("wkhelper.video.snapshot.CdpClient.call", side_effect=fake_call),
    ):
        frames = await capture_video_frames(
            web_url="https://test.com/video/1",
            cookies={},
            cookie_domain=".test.com",
            timestamps=[25.0],
            output_dir=out_dir,
        )

    assert len(frames) == 1
    assert frames[0].timestamp == 25.0
    assert frames[0].file_path is None
    assert frames[0].error == "seek_timeout"


@pytest.mark.parametrize("error", ["seek_restricted", "seek_timeout", "frame_not_ready", "no_video"])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.asyncio
async def test_video_snapshot_cmd_partial_and_total_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: str, partial: bool
) -> None:
    from wkhelper.main import async_main

    frames = [VideoSnapshotFrame(timestamp=20.0, actual_time=2.0, error=error)]
    if partial:
        frames.insert(0, VideoSnapshotFrame(timestamp=10.0, actual_time=10.0, file_path="/tmp/f1.jpg", size_bytes=100))
    monkeypatch.setattr("wkhelper.cli.commands.video.capture_video_frames", AsyncMock(return_value=frames))

    # 假造 _open_course
    class MockPlatform:
        api_base = "https://www.xuetangx.com"
        cookie_domain = ".xuetangx.com"
        client = AsyncMock(cookies={})
        user = None
        account_id = "test-user"
        platform_code = "xtzx"

        def format_web_task_url(self, course: object, leaf_id: object, leaf_type: str) -> str:
            return "https://www.xuetangx.com/learn/1/video/1"

    class MockCourseCtx:
        async def __aenter__(self) -> tuple[MockPlatform, object]:
            return MockPlatform(), MagicMock()

        async def __aexit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr("wkhelper.cli.commands.video._open_course", lambda *a, **kw: MockCourseCtx())

    code = await async_main(
        [
            "--format",
            "json",
            "video",
            "snapshot",
            "--course",
            "1001",
            "--video",
            "2001",
            "--timestamps",
            "10,20" if partial else "20",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert code == (7 if partial else 6)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == ("PARTIAL_FAILURE" if partial else "PLATFORM_ERROR")
    assert envelope["error"]["retryable"] is False
    failed = envelope["data"]["frames"][-1]
    assert failed["timestamp"] == 20.0
    assert failed["actual_time"] == 2.0
    assert failed["file_path"] is None
    assert failed["error"] == error
    if partial:
        assert envelope["data"]["frames"][0]["file_path"] == "/tmp/f1.jpg"
    if error in ("seek_restricted", "seek_timeout"):
        assert envelope["next_actions"][0]["argv"] == [
            "wkhelper",
            "video",
            "learn",
            "--course",
            "1001",
            "--video",
            "2001",
            "--dry-run",
            "--platform",
            "xtzx",
            "--account",
            "test-user",
        ]
    else:
        assert envelope["next_actions"] == []


@pytest.mark.asyncio
async def test_capture_video_frames_warm_pool_skip_navigate(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="test_snap_warm_"))
    session_dir = Path(tempfile.mkdtemp(prefix="test_snap_sess_"))
    out_dir = Path(tempfile.mkdtemp(prefix="test_snap_out_"))

    mock_proc = BrowserProcess(
        pid=os.getpid(),
        port=9555,
        profile_dir=str(temp_dir),
        mode="headless",
        proc=MagicMock(),
        created_at=time.time(),
    )

    monkeypatch.setattr(
        "wkhelper.infra.browser.process.BrowserManager.launch_browser",
        lambda **kwargs: mock_proc,
    )
    monkeypatch.setattr(
        "wkhelper.infra.browser.process.BrowserManager.wait_debugger",
        AsyncMock(return_value=("ws://browser", "ws://page_warm")),
    )
    monkeypatch.setattr(
        "wkhelper.video.session_pool.spawn_watchdog",
        lambda **kwargs: 1234,
    )
    monkeypatch.setattr(
        "wkhelper.video.session_pool.check_cdp_alive",
        lambda port, timeout=1.0: True,
    )

    class MockWs:
        async def send_json(self, data: dict[str, object]) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr(
        "curl_cffi.requests.AsyncSession.ws_connect",
        AsyncMock(return_value=MockWs()),
    )

    dummy_jpeg = b"\xff\xd8\xff\xe0testjpeg"
    dummy_b64 = base64.b64encode(dummy_jpeg).decode("ascii")
    nav_calls: list[str] = []

    async def fake_call(*args: object, **kw: object) -> dict[str, object]:
        method = str(args[1] if len(args) > 1 else (args[0] if args else ""))
        params = args[2] if len(args) > 2 else kw.get("params")
        param_dict = params if isinstance(params, dict) else {}
        if method == "Page.navigate":
            url = str(param_dict.get("url", ""))
            nav_calls.append(url)
            return {}
        if method == "Runtime.evaluate":
            expr = str(param_dict.get("expression", ""))
            if "duration" in expr and "hasDuration" in expr:
                return {
                    "result": {
                        "value": {
                            "found": True,
                            "ready": True,
                            "duration": 100.0,
                            "videoWidth": 1280,
                            "videoHeight": 720,
                        }
                    }
                }
            if "canvas" in expr:
                return {
                    "result": {
                        "value": {
                            "ok": True,
                            "dataUrl": f"data:image/jpeg;base64,{dummy_b64}",
                            "width": 1280,
                            "height": 720,
                        }
                    }
                }
        return {"result": {}}

    monkeypatch.setattr("wkhelper.infra.browser.cdp.CdpClient.call", fake_call)

    # 第一次调用：冷启动，执行 Page.navigate
    frames1 = await capture_video_frames(
        web_url="https://example.com/video/1",
        cookies={"sid": "123"},
        cookie_domain=".example.com",
        timestamps=[10.0],
        course_id="c_test",
        video_id="v_test",
        use_cache=True,
        session_dir=session_dir,
        output_dir=out_dir,
    )
    assert len(frames1) == 1
    assert len(nav_calls) == 1

    # 第二次调用：命中热池且页面视频就绪，跳过 Page.navigate
    frames2 = await capture_video_frames(
        web_url="https://example.com/video/1",
        cookies={"sid": "123"},
        cookie_domain=".example.com",
        timestamps=[20.0],
        course_id="c_test",
        video_id="v_test",
        use_cache=True,
        session_dir=session_dir,
        output_dir=out_dir,
    )
    assert len(frames2) == 1
    # 确认 Page.navigate 调用次数未增加（依然为 1）
    assert len(nav_calls) == 1


@pytest.mark.asyncio
async def test_capture_video_frames_no_cache_bypasses_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    session_dir = Path(tempfile.mkdtemp(prefix="test_snap_nocache_sess_"))
    out_dir = Path(tempfile.mkdtemp(prefix="test_snap_nocache_out_"))

    mock_proc = BrowserProcess(
        pid=1212,
        port=9666,
        profile_dir="/tmp/ephemeral",
        mode="headless",
        proc=MagicMock(),
        created_at=time.time(),
        page_ws="ws://page_ephemeral",
    )

    class MockContextManager:
        async def __aenter__(self) -> BrowserProcess:
            return mock_proc

        async def __aexit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(
        "wkhelper.infra.browser.process.BrowserManager.acquire_session",
        lambda *a, **kw: MockContextManager(),
    )

    class MockWs:
        async def send_json(self, data: dict[str, object]) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr(
        "curl_cffi.requests.AsyncSession.ws_connect",
        AsyncMock(return_value=MockWs()),
    )

    dummy_jpeg = b"\xff\xd8\xff\xe0testjpeg"
    dummy_b64 = base64.b64encode(dummy_jpeg).decode("ascii")

    async def fake_call(*args: object, **kw: object) -> dict[str, object]:
        method = str(args[1] if len(args) > 1 else (args[0] if args else ""))
        params = args[2] if len(args) > 2 else kw.get("params")
        param_dict = params if isinstance(params, dict) else {}
        if method == "Runtime.evaluate":
            expr = str(param_dict.get("expression", ""))
            if "duration" in expr:
                return {"result": {"value": {"found": True, "ready": True, "duration": 50.0}}}
            if "canvas" in expr:
                return {"result": {"value": {"ok": True, "dataUrl": f"data:image/jpeg;base64,{dummy_b64}", "width": 800, "height": 600}}}
        return {"result": {}}

    monkeypatch.setattr("wkhelper.infra.browser.cdp.CdpClient.call", fake_call)

    frames = await capture_video_frames(
        web_url="https://example.com/video/1",
        cookies={},
        cookie_domain="",
        timestamps=[5.0],
        course_id="c_test",
        video_id="v_test",
        use_cache=False,
        session_dir=session_dir,
        output_dir=out_dir,
    )
    assert len(frames) == 1
    # 确认 session_dir 中没有创建任何 session_*.json
    assert list(session_dir.glob("session_*.json")) == []


@pytest.mark.asyncio
async def test_capture_video_frames_warm_pool_failure_falls_back_to_ephemeral(monkeypatch: pytest.MonkeyPatch) -> None:
    """当热池提取发生异常时，应清理热池会话并优雅降级至 ephemeral 临时浏览器模式完成截帧。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_fallback_"))
    try:
        mock_warm_proc = BrowserProcess(
            pid=1111,
            port=9111,
            profile_dir=str(temp_dir / "warm_prof"),
            mode="headless",
            proc=MagicMock(),
            created_at=time.time(),
        )
        mock_ephemeral_proc = BrowserProcess(
            pid=2222,
            port=9222,
            profile_dir=str(temp_dir / "eph_prof"),
            mode="headless",
            proc=MagicMock(),
            page_ws="ws://page_ephemeral",
            created_at=time.time(),
        )
        # 热池启动返回 mock_warm_proc
        monkeypatch.setattr(
            "wkhelper.infra.browser.process.BrowserManager.launch_browser",
            lambda **kwargs: mock_warm_proc,
        )

        class MockEphemeralCtx:
            async def __aenter__(self) -> BrowserProcess:
                return mock_ephemeral_proc

            async def __aexit__(self, *args: object) -> None:
                pass

        monkeypatch.setattr(
            "wkhelper.infra.browser.process.BrowserManager.acquire_session",
            lambda **kwargs: MockEphemeralCtx(),
        )
        monkeypatch.setattr(
            "wkhelper.infra.browser.process.BrowserManager.wait_debugger",
            AsyncMock(return_value=("ws://browser", "ws://page_warm")),
        )
        monkeypatch.setattr(
            "wkhelper.video.session_pool.spawn_watchdog",
            lambda **kwargs: 3333,
        )
        monkeypatch.setattr(
            "wkhelper.video.session_pool.check_cdp_alive",
            lambda port, timeout=1.0: True,
        )

        class MockWs:
            async def send_json(self, data: dict[str, object]) -> None:
                pass

            async def close(self) -> None:
                pass

        monkeypatch.setattr(
            "curl_cffi.requests.AsyncSession.ws_connect",
            AsyncMock(return_value=MockWs()),
        )

        extract_calls: list[str] = []

        async def mock_extract_frames_cdp(**kwargs: object) -> list[VideoSnapshotFrame]:
            # 区分首次（热池）和二次（临时）
            if len(extract_calls) == 0:
                extract_calls.append("warm")
                raise RuntimeError("热池会话发生网络异常")
            extract_calls.append("ephemeral")
            return [VideoSnapshotFrame(timestamp=5.0, file_path=str(temp_dir / "f.jpg"), size_bytes=100)]

        monkeypatch.setattr("wkhelper.video.snapshot._extract_frames_cdp", mock_extract_frames_cdp)

        frames = await capture_video_frames(
            web_url="https://example.com/video",
            cookies={},
            cookie_domain="",
            timestamps=[5.0],
            course_id="c_fail",
            video_id="v_fail",
            session_dir=temp_dir,
            use_cache=True,
        )

        assert len(frames) == 1
        assert frames[0].timestamp == 5.0
        assert extract_calls == ["warm", "ephemeral"]
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_capture_video_frames_lock_timeout_preserves_session_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """当获取文件锁超时时，不得删除已存在的 session_file，并应优雅回退至临时浏览器截帧。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_lock_timeout_preserve_"))
    session_dir = Path(tempfile.mkdtemp(prefix="test_lock_sess_"))
    try:
        from wkhelper.video.session_pool import get_session_file

        sess_file = get_session_file("c_pres", "v_pres", session_dir=session_dir)
        sess_file.write_text('{"course_id": "c_pres", "video_id": "v_pres"}', encoding="utf-8")

        # 模拟 async_file_lock 超时
        @contextlib.asynccontextmanager
        async def fake_async_file_lock(*args: object, **kwargs: object):
            if True:
                raise TimeoutError("获取会话文件锁超时")
            yield

        monkeypatch.setattr("wkhelper.video.snapshot.async_file_lock", fake_async_file_lock)

        mock_ephemeral_proc = BrowserProcess(
            pid=4444,
            port=9444,
            profile_dir=str(temp_dir / "eph_prof"),
            mode="headless",
            proc=MagicMock(),
            page_ws="ws://page_ephemeral",
            created_at=time.time(),
        )

        class MockEphemeralCtx:
            async def __aenter__(self) -> BrowserProcess:
                return mock_ephemeral_proc

            async def __aexit__(self, *args: object) -> None:
                pass

        monkeypatch.setattr(
            "wkhelper.infra.browser.process.BrowserManager.acquire_session",
            lambda **kwargs: MockEphemeralCtx(),
        )

        class MockWs:
            async def send_json(self, data: dict[str, object]) -> None:
                pass

            async def close(self) -> None:
                pass

        monkeypatch.setattr(
            "curl_cffi.requests.AsyncSession.ws_connect",
            AsyncMock(return_value=MockWs()),
        )

        async def mock_extract(**kwargs: object) -> list[VideoSnapshotFrame]:
            return [VideoSnapshotFrame(timestamp=1.0, file_path=str(temp_dir / "f.jpg"), size_bytes=50)]

        monkeypatch.setattr("wkhelper.video.snapshot._extract_frames_cdp", mock_extract)

        frames = await capture_video_frames(
            web_url="https://example.com/video",
            cookies={},
            cookie_domain="",
            timestamps=[1.0],
            course_id="c_pres",
            video_id="v_pres",
            session_dir=session_dir,
            use_cache=True,
        )

        assert len(frames) == 1
        assert frames[0].timestamp == 1.0
        # 核心断言：session_file 依然存在且未被删除！
        assert sess_file.exists()
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(session_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_extract_frames_cdp_op_deadline_enforcement(monkeypatch: pytest.MonkeyPatch) -> None:
    """当截帧操作截止时间到达时，后续帧应标记超时而非死循环。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_deadline_"))
    try:

        async def fake_call(method: str, params: dict[str, object] | None = None, **kw: object) -> dict[str, object]:
            if method == "Runtime.evaluate":
                return {"result": {"value": {"found": True, "ready": True, "duration": 100.0}}}
            return {"result": {}}

        cdp_mock = MagicMock()
        cdp_mock.call = AsyncMock(side_effect=fake_call)
        loop = asyncio.get_running_loop()
        start_time = loop.time()
        # 设置 deadline 为过去时间，模拟已超时
        op_deadline = start_time - 1.0

        frames = await _extract_frames_cdp(
            cdp=cdp_mock,
            web_url="https://example.com/video",
            is_new=False,
            timestamps=[1.0, 2.0],
            width=1280,
            height=720,
            quality_int=85,
            quality_ratio=0.85,
            save_dir=temp_dir,
            op_deadline=op_deadline,
        )

        assert len(frames) == 2
        assert "超时" in str(frames[0].error)
        assert "超时" in str(frames[1].error)
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)
