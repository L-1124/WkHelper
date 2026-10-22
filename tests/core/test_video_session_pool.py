"""视频截帧 Warm Session 进程池与 Watchdog 守护机制单元测试。"""

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wkhelper.domain.video import VideoSnapshotFrame
from wkhelper.infra.browser.process import BrowserManager, BrowserProcess
from wkhelper.video.session_pool import (
    VideoWarmSession,
    _watchdog_main,
    acquire_warm_session,
    async_file_lock,
    check_cdp_alive,
    cleanup_all_warm_sessions,
    close_warm_session,
    file_lock,
    get_active_page_ws,
    get_default_video_session_dir,
    get_or_create_warm_session,
    get_session_file,
    is_pid_alive,
    run_watchdog,
    spawn_watchdog,
    terminate_process_tree,
    touch_session,
)


def test_video_warm_session_model() -> None:
    now = time.time()
    session = VideoWarmSession(
        course_id="1001",
        video_id="2001",
        pid=1234,
        port=9222,
        profile_dir="/tmp/profile",
        page_ws="ws://127.0.0.1:9222/page",
        browser_ws="ws://127.0.0.1:9222/browser",
        last_active_at=now,
        ttl_seconds=120.0,
        web_url="https://example.com/video",
        watchdog_pid=5678,
        account_id="user_123",
        platform="ykt",
    )

    data = session.model_dump()
    assert data["course_id"] == "1001"
    assert data["video_id"] == "2001"
    assert data["pid"] == 1234
    assert data["port"] == 9222
    assert data["watchdog_pid"] == 5678
    assert data["account_id"] == "user_123"
    assert data["platform"] == "ykt"

    restored = VideoWarmSession.model_validate(data)
    assert restored.course_id == "1001"
    assert restored.video_id == "2001"
    assert restored.pid == 1234
    assert restored.port == 9222
    assert restored.page_ws == "ws://127.0.0.1:9222/page"
    assert restored.browser_ws == "ws://127.0.0.1:9222/browser"
    assert restored.last_active_at == now
    assert restored.ttl_seconds == 120.0
    assert restored.web_url == "https://example.com/video"
    assert restored.watchdog_pid == 5678
    assert restored.account_id == "user_123"
    assert restored.platform == "ykt"


def test_session_path_and_sanitization() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="test_session_path_"))
    try:
        session_file = get_session_file(
            course_id="97330:29593706",
            video_id="75369628",
            session_dir=temp_dir,
        )
        assert session_file.name == "session_97330_29593706_75369628.json"
        assert session_file.parent == temp_dir

        # 验证带平台与账号隔离的文件名
        partitioned = get_session_file(
            course_id="101",
            video_id="202",
            session_dir=temp_dir,
            account_id="user_abc",
            platform="xtzx",
        )
        assert partitioned.name == "session_xtzx_user_abc_101_202.json"

        special_file = get_session_file(
            course_id="test/path?foo=bar",
            video_id="video#1",
            session_dir=temp_dir,
        )
        assert "/" not in special_file.name
        assert "?" not in special_file.name
        assert "#" not in special_file.name
        default_dir = get_default_video_session_dir(temp_dir)
        assert default_dir == temp_dir / "video_sessions"
        assert default_dir.exists()
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_file_lock_lifecycle() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="test_file_lock_"))
    lock_target = temp_dir / "target.json"
    try:
        with file_lock(lock_target, timeout=2.0):
            assert Path(str(lock_target) + ".lock").exists()

        # 验证同线程可重入加锁
        with file_lock(lock_target, timeout=2.0):
            with file_lock(lock_target, timeout=2.0):
                pass

        # 验证跨线程互斥与超时
        import threading

        thread_timed_out: list[bool] = []

        def try_lock() -> None:
            try:
                with file_lock(lock_target, timeout=0.1):
                    pass
            except TimeoutError:
                thread_timed_out.append(True)

        with file_lock(lock_target, timeout=2.0):
            t = threading.Thread(target=try_lock)
            t.start()
            t.join()
            assert thread_timed_out == [True]
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_pid_and_cdp_liveness() -> None:
    assert is_pid_alive(os.getpid()) is True
    assert is_pid_alive(-1) is False
    assert is_pid_alive(0) is False
    assert is_pid_alive(999999) is False

    assert check_cdp_alive(65530) is False

    # 模拟响应有效的 CDP
    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"Browser": "Chrome/120.0.0.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9222"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_url.return_value = mock_resp

        assert check_cdp_alive(9222) is True


def test_get_active_page_ws() -> None:
    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'[{"type": "page", "webSocketDebuggerUrl": "ws://page_ws_target"}]'
        mock_resp.__enter__.return_value = mock_resp
        mock_url.return_value = mock_resp

        assert get_active_page_ws(9222, "default_ws") == "ws://page_ws_target"

    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        assert get_active_page_ws(9222, "default_ws") == "default_ws"


def test_terminate_process_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        with patch("subprocess.run") as mock_run:
            terminate_process_tree(12345)
            mock_run.assert_called_once_with(
                ["taskkill", "/PID", "12345", "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
            )
    else:
        with patch("os.kill") as mock_kill, patch("os.getpgid", return_value=999), patch("os.getpgrp", return_value=111):
            terminate_process_tree(12345)
            mock_kill.assert_called()


def test_touch_session() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="test_touch_"))
    meta_file = temp_dir / "session.json"
    try:
        initial_time = 1000.0
        meta_file.write_text(
            json.dumps({"course_id": "c1", "video_id": "v1", "last_active_at": initial_time}),
            encoding="utf-8",
        )
        touch_session(meta_file)
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        assert data["last_active_at"] > initial_time
        assert abs(data["last_active_at"] - time.time()) < 5.0

        # 不存在的文件不报错
        touch_session(temp_dir / "non_existent.json")
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_watchdog_exits_when_pid_dead() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="test_wd_dead_"))
    meta_file = temp_dir / "session.json"
    profile_dir = temp_dir / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        meta_file.write_text(json.dumps({"course_id": "c1", "video_id": "v1"}), encoding="utf-8")
        # 传入一个确定死亡的 PID
        run_watchdog(
            port=9999,
            pid=999999,
            profile_dir=str(profile_dir),
            meta_file=str(meta_file),
            ttl_seconds=10.0,
            check_interval=0.01,
            max_ticks=5,
        )
        assert not meta_file.exists()
        assert not profile_dir.exists()
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_watchdog_exits_when_meta_file_missing() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="test_wd_missing_"))
    meta_file = temp_dir / "session.json"
    profile_dir = temp_dir / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        with (
            patch("wkhelper.video.session_pool.is_pid_alive", return_value=True),
            patch("wkhelper.video.session_pool.terminate_process_tree") as mock_term,
        ):
            # meta_file 并不存在
            run_watchdog(
                port=9999,
                pid=12345,
                profile_dir=str(profile_dir),
                meta_file=str(meta_file),
                ttl_seconds=10.0,
                check_interval=0.01,
                max_ticks=5,
            )
            mock_term.assert_called_once_with(12345)
            assert not profile_dir.exists()
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_watchdog_exits_on_ttl_timeout() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="test_wd_ttl_"))
    meta_file = temp_dir / "session.json"
    profile_dir = temp_dir / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        expired_time = time.time() - 300.0
        meta_file.write_text(
            json.dumps({"course_id": "c1", "video_id": "v1", "last_active_at": expired_time, "ttl_seconds": 60.0}),
            encoding="utf-8",
        )
        with (
            patch("wkhelper.video.session_pool.is_pid_alive", return_value=True),
            patch("wkhelper.video.session_pool.terminate_process_tree") as mock_term,
        ):
            run_watchdog(
                port=9999,
                pid=12345,
                profile_dir=str(profile_dir),
                meta_file=str(meta_file),
                ttl_seconds=60.0,
                check_interval=0.01,
                max_ticks=5,
            )
            mock_term.assert_called_once_with(12345)
            assert not meta_file.exists()
            assert not profile_dir.exists()
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_clean_session_resources_and_cleanup_all() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="test_cleanup_all_"))
    try:
        sess1_file = temp_dir / "session_c1_v1.json"
        sess2_file = temp_dir / "session_c2_v2.json"

        p1_dir = temp_dir / "p1"
        p2_dir = temp_dir / "p2"
        p1_dir.mkdir()
        p2_dir.mkdir()

        s1 = VideoWarmSession(course_id="c1", video_id="v1", pid=0, port=9001, profile_dir=str(p1_dir))
        s2 = VideoWarmSession(course_id="c2", video_id="v2", pid=0, port=9002, profile_dir=str(p2_dir))

        sess1_file.write_text(json.dumps(s1.model_dump()), encoding="utf-8")
        sess2_file.write_text(json.dumps(s2.model_dump()), encoding="utf-8")

        close_warm_session(s1, session_dir=temp_dir)
        assert not sess1_file.exists()
        assert not p1_dir.exists()

        cleaned_count = cleanup_all_warm_sessions(session_dir=temp_dir)
        assert cleaned_count == 1
        assert not sess2_file.exists()
        assert not p2_dir.exists()
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_get_or_create_warm_session_cold_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="test_cold_"))
    try:
        mock_proc = BrowserProcess(
            pid=7777,
            port=9333,
            profile_dir=str(temp_dir / "browser_prof"),
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
            AsyncMock(return_value=("ws://browser", "ws://page")),
        )
        monkeypatch.setattr(
            "wkhelper.video.session_pool.spawn_watchdog",
            lambda **kwargs: 8888,
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
        monkeypatch.setattr(
            "wkhelper.infra.browser.cdp.CdpClient.call",
            AsyncMock(return_value={}),
        )

        session, is_new = await get_or_create_warm_session(
            course_id="101",
            video_id="202",
            web_url="https://example.com/learn",
            cookies={"sid": "abc"},
            cookie_domain=".example.com",
            session_dir=temp_dir,
        )

        assert is_new is True
        assert session.course_id == "101"
        assert session.video_id == "202"
        assert session.pid == 7777
        assert session.port == 9333
        assert session.watchdog_pid == 8888

        session_file = get_session_file("101", "202", session_dir=temp_dir)
        assert session_file.exists()
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_get_or_create_warm_session_warm_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="test_warm_hit_"))
    try:
        session_file = get_session_file("101", "202", session_dir=temp_dir)
        initial_sess = VideoWarmSession(
            course_id="101",
            video_id="202",
            pid=os.getpid(),  # 当前测试进程是活的
            port=9444,
            profile_dir=str(temp_dir / "prof"),
            page_ws="ws://page_warm",
            browser_ws="ws://browser_warm",
            last_active_at=time.time() - 10.0,
            ttl_seconds=120.0,
            web_url="https://example.com/learn",
        )
        session_file.write_text(json.dumps(initial_sess.model_dump()), encoding="utf-8")

        monkeypatch.setattr("wkhelper.video.session_pool.check_cdp_alive", lambda port, timeout=1.0: True)

        session, is_new = await get_or_create_warm_session(
            course_id="101",
            video_id="202",
            web_url="https://example.com/learn",
            cookies={},
            cookie_domain="",
            session_dir=temp_dir,
        )

        assert is_new is False
        assert session.page_ws == "ws://page_warm"
        assert session.pid == os.getpid()
        assert session.last_active_at > initial_sess.last_active_at

        # 测试 acquire_warm_session 便捷入口
        acquired = await acquire_warm_session(
            course_id="101",
            video_id="202",
            web_url="https://example.com/learn",
            cookies={},
            cookie_domain="",
            session_dir=temp_dir,
        )
        assert acquired.page_ws == "ws://page_warm"
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_spawn_watchdog_and_cli_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 9911
        mock_popen.return_value = mock_proc

        pid = spawn_watchdog(
            port=9222,
            pid=1234,
            profile_dir="/tmp/p",
            meta_file="/tmp/m.json",
            ttl_seconds=60.0,
        )
        assert pid == 9911
        mock_popen.assert_called_once()

    with patch("sys.argv", ["pool", "--port", "9222", "--pid", "1234", "--profile-dir", "/tmp/p", "--meta-file", "/tmp/m", "--ttl", "60"]):
        with patch("wkhelper.video.session_pool.run_watchdog") as mock_run:
            _watchdog_main()
            mock_run.assert_called_once_with(
                port=9222,
                pid=1234,
                profile_dir="/tmp/p",
                meta_file="/tmp/m",
                ttl_seconds=60.0,
                check_interval=2.0,
            )


@pytest.mark.asyncio
async def test_file_lock_async_task_contention() -> None:
    """同一线程内不同 asyncio 任务竞争同一文件锁时应互斥并超时，而非误判为重入。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_async_lock_"))
    lock_target = temp_dir / "target.json"
    try:
        task1_acquired = asyncio.Event()
        task2_timed_out: list[bool] = []

        async def holder() -> None:
            with file_lock(lock_target, timeout=2.0):
                task1_acquired.set()
                await asyncio.sleep(0.3)

        async def contender() -> None:
            await task1_acquired.wait()
            try:
                with file_lock(lock_target, timeout=0.05):
                    pass
            except TimeoutError:
                task2_timed_out.append(True)

        await asyncio.gather(holder(), contender())
        assert task2_timed_out == [True]
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_watchdog_corrupted_meta_file() -> None:
    """当元数据文件损坏时，watchdog 连续尝试失败后应主动终止进程并清理。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_wd_corrupt_"))
    meta_file = temp_dir / "session.json"
    profile_dir = temp_dir / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        meta_file.write_text("{invalid json corrupt content...", encoding="utf-8")
        with (
            patch("wkhelper.video.session_pool.is_pid_alive", return_value=True),
            patch("wkhelper.video.session_pool.terminate_process_tree") as mock_term,
        ):
            run_watchdog(
                port=9999,
                pid=12345,
                profile_dir=str(profile_dir),
                meta_file=str(meta_file),
                ttl_seconds=60.0,
                check_interval=0.01,
                max_ticks=10,
            )
            mock_term.assert_called_once_with(12345)
            assert not meta_file.exists()
            assert not profile_dir.exists()
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_get_or_create_warm_session_cancelled_cleans_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """在创建会话期间遭遇 CancelledError 时，应清理刚刚启动的浏览器进程与目录。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_cancel_clean_"))
    try:
        mock_proc = BrowserProcess(
            pid=9876,
            port=9444,
            profile_dir=str(temp_dir / "browser_prof"),
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
            AsyncMock(side_effect=asyncio.CancelledError("task cancelled")),
        )
        cleaned_sessions: list[VideoWarmSession] = []

        def fake_clean(sess: VideoWarmSession, meta: object = None) -> None:
            cleaned_sessions.append(sess)

        monkeypatch.setattr("wkhelper.video.session_pool.clean_session_resources", fake_clean)

        with pytest.raises(asyncio.CancelledError):
            await get_or_create_warm_session(
                course_id="c_cancel",
                video_id="v_cancel",
                web_url="https://example.com",
                cookies={},
                cookie_domain="",
                session_dir=temp_dir,
            )

        assert len(cleaned_sessions) == 1
        assert cleaned_sessions[0].pid == 9876
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_lock_file_persistent_after_cleanup() -> None:
    """验证 clean_session_resources 不会删除 .lock 文件，避免 Unix 下的 inode race。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_lock_persist_"))
    try:
        meta_file = temp_dir / "session_c1_v1.json"
        meta_file.write_text("{}", encoding="utf-8")
        lock_file = Path(str(meta_file) + ".lock")
        lock_file.write_text("", encoding="utf-8")

        s = VideoWarmSession(course_id="c1", video_id="v1", pid=0, port=9001, profile_dir="")
        close_warm_session(s, session_dir=temp_dir)
        assert not meta_file.exists()
        assert lock_file.exists()
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_file_lock_acquired_flag_on_failure() -> None:
    """验证获取锁失败时不会错误释放或破坏锁持有者状态。"""
    import threading

    temp_dir = Path(tempfile.mkdtemp(prefix="test_lock_fail_"))
    lock_target = temp_dir / "target.json"
    try:
        with file_lock(lock_target, timeout=2.0):
            err_raised: list[bool] = []

            def worker() -> None:
                try:
                    with file_lock(lock_target, timeout=0.05):
                        pass
                except TimeoutError:
                    err_raised.append(True)

            t = threading.Thread(target=worker)
            t.start()
            t.join()
            assert err_raised == [True]
            # 确认主线程的锁依然持有，没有被失败的线程 pop 掉
            norm_path = str(Path(str(lock_target) + ".lock").resolve())
            from wkhelper.video.session_pool import _FILE_LOCK_OWNERS

            assert norm_path in _FILE_LOCK_OWNERS
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_async_file_lock_holder_await_competitor_succeeds() -> None:
    """行为测试：持锁任务 await 挂起时，等待的竞争者不应卡死事件循环，并在持锁者释放后成功获取锁。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_async_lock_succ_"))
    lock_target = temp_dir / "target.json"
    try:
        task1_acquired = asyncio.Event()
        task1_released = asyncio.Event()
        task2_acquired = asyncio.Event()
        order: list[str] = []

        async def holder() -> None:
            async with async_file_lock(lock_target, timeout=2.0):
                order.append("holder_acquired")
                task1_acquired.set()
                # 持锁并挂起让出事件循环
                await asyncio.sleep(0.1)
            order.append("holder_released")
            task1_released.set()

        async def competitor() -> None:
            await task1_acquired.wait()
            # 竞争者非阻塞等待锁
            async with async_file_lock(lock_target, timeout=2.0):
                order.append("competitor_acquired")
                assert task1_released.is_set()
                task2_acquired.set()

        await asyncio.gather(holder(), competitor())
        assert task2_acquired.is_set()
        assert order == ["holder_acquired", "holder_released", "competitor_acquired"]
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_async_file_lock_cancellation_safety() -> None:
    """行为测试：竞争任务在非阻塞等待锁期间被取消，不得遗留持锁状态或后台线程，后续任务仍可成功获取。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_async_lock_cancel_"))
    lock_target = temp_dir / "target.json"
    try:
        task1_acquired = asyncio.Event()
        task2_started = asyncio.Event()
        task3_success = asyncio.Event()

        async def holder() -> None:
            async with async_file_lock(lock_target, timeout=2.0):
                task1_acquired.set()
                await asyncio.sleep(0.2)

        async def competitor() -> None:
            await task1_acquired.wait()
            task2_started.set()
            async with async_file_lock(lock_target, timeout=5.0):
                pass

        holder_task = asyncio.create_task(holder())
        comp_task = asyncio.create_task(competitor())
        await task2_started.wait()
        # 竞争者处于 await asyncio.sleep 等待状态时取消它
        await asyncio.sleep(0.02)
        comp_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await comp_task
        assert comp_task.cancelled()

        await holder_task

        # 后续任务应能立即成功获取锁
        async with async_file_lock(lock_target, timeout=1.0):
            task3_success.set()

        assert task3_success.is_set()
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_cleanup_all_warm_sessions_skips_timeout_preserves_metadata_cleans_corrupted() -> None:
    """行为测试：cleanup_all_warm_sessions 遇到锁超时必须跳过不能删元数据；锁取得后坏文件仍可清理。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_cleanup_safe_"))
    try:
        active_sess_file = temp_dir / "session_active.json"
        corrupt_sess_file = temp_dir / "session_corrupt.json"

        active_sess = VideoWarmSession(course_id="c_act", video_id="v_act", pid=99999, port=9099, profile_dir="")
        active_sess_file.write_text(json.dumps(active_sess.model_dump()), encoding="utf-8")
        corrupt_sess_file.write_text("{invalid json corrupt content...", encoding="utf-8")

        # 模拟 active_sess_file 获取锁时超时，而 corrupt_sess_file 正常获取锁
        real_file_lock = file_lock

        @contextlib.contextmanager
        def fake_lock(path: Path | str, timeout: float = 3.0):
            if "session_active" in str(path):
                raise TimeoutError("获取会话文件锁超时")
            with real_file_lock(path, timeout=timeout):
                yield

        with patch("wkhelper.video.session_pool.file_lock", side_effect=fake_lock):
            cleaned = cleanup_all_warm_sessions(session_dir=temp_dir)

            # 活跃会话因为锁超时被跳过，元数据未被删除！
            assert active_sess_file.exists()
            # 损坏的会话取得了锁，被正常清理！
            assert not corrupt_sess_file.exists()
            assert cleaned == 0
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_default_snapshot_identity_selects_own_cached_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """省略 CLI 身份时，切换用户必须命中各自已认证的缓存，而非共用旧会话。"""
    from wkhelper.cli.commands.video import video_snapshot_cmd
    from wkhelper.domain.accounts import UserInfo
    from wkhelper.domain.courses import Course

    for account, pid in (("10001", 101), ("20002", 202)):
        session = VideoWarmSession(
            course_id="c1", video_id="v1", pid=pid, port=9000 + pid, profile_dir="", account_id=account, platform="ykt"
        )
        path = get_session_file("c1", "v1", session_dir=tmp_path, account_id=account, platform="ykt")
        path.write_text(json.dumps(session.model_dump()), encoding="utf-8")

    monkeypatch.setattr("wkhelper.video.session_pool.is_pid_alive", lambda pid: True)
    monkeypatch.setattr("wkhelper.video.session_pool.check_cdp_alive", lambda port: True)

    def forbid_launch(**kwargs: object):
        raise AssertionError("账号对应缓存应已命中，不应创建其他浏览器")

    monkeypatch.setattr(BrowserManager, "launch_browser", forbid_launch)
    selected_pids: list[int] = []

    async def capture_using_real_cache(**kwargs: Any) -> list[VideoSnapshotFrame]:
        session, is_new = await get_or_create_warm_session(
            course_id=kwargs["course_id"],
            video_id=kwargs["video_id"],
            web_url=kwargs["web_url"],
            cookies={},
            cookie_domain=".yuketang.cn",
            session_dir=tmp_path,
            account_id=kwargs["account_id"],
            platform=kwargs["platform"],
        )
        assert not is_new
        selected_pids.append(session.pid)
        return []

    monkeypatch.setattr("wkhelper.cli.commands.video.capture_video_frames", capture_using_real_cache)

    class AuthenticatedPlatform:
        api_base = "https://www.yuketang.cn"
        platform_code = "ykt"
        client = MagicMock(cookies={})

        def __init__(self, account: str) -> None:
            self.user = UserInfo(id=account, name="user")
            self.account_id = account

        def format_web_task_url(self, *args: object) -> str:
            return "https://www.yuketang.cn/video/v1"

    current_account = "10001"

    @contextlib.asynccontextmanager
    async def open_course(*args: object, **kwargs: object):
        yield AuthenticatedPlatform(current_account), Course(id="c1", name="course", platform_id="ykt")

    monkeypatch.setattr("wkhelper.cli.commands.video._open_course", open_course)
    await video_snapshot_cmd(course="c1", video="v1", timestamps="10")
    current_account = "20002"
    await video_snapshot_cmd(course="c1", video="v1", timestamps="10")
    assert selected_pids == [101, 202]


def test_session_restore_normalizes_legacy_ids_but_rejects_invalid_pid() -> None:
    from pydantic import ValidationError

    session = VideoWarmSession.model_validate({"course_id": 10, "video_id": 20, "pid": "123", "port": "9222", "account_id": 42})
    assert (session.course_id, session.video_id, session.pid, session.port, session.account_id) == ("10", "20", 123, 9222, "42")
    with pytest.raises(ValidationError):
        VideoWarmSession.model_validate({"pid": "broken-pid"})


@pytest.mark.parametrize("metadata, ttl", [({}, 120.0), ({"last_active_at": 1000.0}, 10.0)])
def test_watchdog_incomplete_metadata_preserves_cleanup_defaults(tmp_path, metadata, ttl) -> None:
    path = tmp_path / "session.json"
    profile = tmp_path / "profile"
    profile.mkdir()
    path.write_text(json.dumps(metadata), encoding="utf-8")
    with (
        patch("wkhelper.video.session_pool.is_pid_alive", return_value=True),
        patch("wkhelper.video.session_pool.time.time", return_value=1020.0),
        patch("wkhelper.video.session_pool.terminate_process_tree") as terminate,
        patch("wkhelper.video.session_pool._try_http_close"),
    ):
        run_watchdog(9999, 12345, str(profile), str(path), ttl_seconds=ttl, check_interval=0, max_ticks=1)
    terminate.assert_called_once_with(12345)
    assert not path.exists()
    assert not profile.exists()
