"""视频帧原生纯净提取引擎。

基于 CDP 与 HTML5 Canvas 原生像素截取，配合单 Tab 连续 seek 调度与控制层 DOM 隐藏降级，
提供高保真、零控件杂音、极速复用的视频帧捕获能力。
"""

import asyncio
import base64
import functools
import importlib.resources
import json
import logging
import tempfile
from pathlib import Path

from wkhelper.domain.video import VideoSnapshotFrame
from wkhelper.infra.browser.cdp import CdpClient
from wkhelper.infra.browser.process import BrowserManager
from wkhelper.infra.http import build_session
from wkhelper.video.session_pool import (
    async_file_lock,
    clean_session_resources,
    get_active_page_ws,
    get_or_create_warm_session,
    get_session_file,
    touch_session,
)

logger = logging.getLogger(__name__)


def _resolve_timestamps(
    duration: float,
    timestamps: list[float] | None = None,
    start: float | None = None,
    end: float | None = None,
    step: float | None = None,
    count: int | None = None,
) -> list[float]:
    """解析、校验并钳位目标抽帧时间戳。"""
    if duration <= 0:
        raise ValueError(f"视频时长无效: duration={duration}")

    if timestamps is not None and len(timestamps) > 0:
        target_ts = [max(0.0, min(float(t), duration)) for t in timestamps]
    else:
        if start is not None and start < 0:
            raise ValueError(f"采样起始时间 start 不能小于 0: {start}")
        if end is not None and end < 0:
            raise ValueError(f"采样结束时间 end 不能小于 0: {end}")
        if start is not None and end is not None and start > end:
            raise ValueError(f"采样起始时间 start ({start}) 不能大于结束时间 end ({end})")
        if step is not None and step <= 0:
            raise ValueError(f"采样步长 step 必须大于 0: {step}")
        if count is not None and count <= 0:
            raise ValueError(f"均匀抽帧数量 count 必须大于 0: {count}")

        s = max(0.0, min(float(start), duration)) if start is not None else 0.0
        e = max(0.0, min(float(end), duration)) if end is not None else duration

        if s > e:
            s, e = e, s

        target_ts: list[float] = []
        if step is not None:
            curr = s
            while curr <= e and len(target_ts) < 30:
                target_ts.append(round(max(0.0, min(curr, duration)), 2))
                curr += step
        elif count is not None:
            c = min(30, count)
            if c == 1:
                target_ts = [round(max(0.0, min((s + e) / 2, duration)), 2)]
            else:
                is_default_range = start is None and end is None
                delta = (e - s) / (c + 1) if is_default_range else (e - s) / max(1, c - 1)
                for i in range(c):
                    point = s + (i + 1) * delta if is_default_range else s + i * delta
                    target_ts.append(round(max(0.0, min(point, duration)), 2))
        else:
            raise ValueError("必须指定 `timestamps`、`step` 或 `count` 采样模式")

    seen: set[float] = set()
    deduped: list[float] = []
    for t in target_ts:
        rounded = round(t, 2)
        if rounded not in seen:
            seen.add(rounded)
            deduped.append(rounded)
    return deduped[:30]


@functools.cache
def _get_script(name: str) -> str:
    """读取并缓存 video/scripts 下的 JS 资源。"""
    resource = importlib.resources.files("wkhelper.video").joinpath("scripts", name)
    try:
        return resource.read_text(encoding="utf-8")
    except Exception as exc:
        raise RuntimeError(f"视频页面脚本缺失: {name}，请检查本地安装完整性") from exc


async def _extract_frames_cdp(
    cdp: CdpClient,
    web_url: str,
    is_new: bool,
    timestamps: list[float] | None,
    width: int,
    height: int,
    quality_int: int,
    quality_ratio: float,
    save_dir: Path,
    op_deadline: float,
    *,
    start: float | None = None,
    end: float | None = None,
    step: float | None = None,
    count: int | None = None,
) -> list[VideoSnapshotFrame]:
    loop = asyncio.get_running_loop()

    def remaining_deadline(cap: float = 15.0) -> float:
        rem = op_deadline - loop.time()
        return min(cap, max(0.001, rem))

    await cdp.call("Page.enable", timeout=remaining_deadline())
    await cdp.call("Runtime.enable", timeout=remaining_deadline())

    video_info: dict[str, object] = {}
    if not is_new:
        ready_expr = f"({_get_script('ready.js')})({{}})"
        eval_res = await cdp.call(
            "Runtime.evaluate",
            {"expression": ready_expr, "returnByValue": True},
            timeout=remaining_deadline(5.0),
        )
        video_info = eval_res.get("result", {}).get("value", {})

    if not (isinstance(video_info, dict) and video_info.get("ready")):
        await cdp.call("Page.navigate", {"url": web_url}, timeout=remaining_deadline())
        remaining = max(1.0, op_deadline - loop.time())
        deadline = loop.time() + min(15.0, remaining)
        while loop.time() < deadline:
            ready_expr = f"({_get_script('ready.js')})({{}})"
            eval_res = await cdp.call(
                "Runtime.evaluate",
                {"expression": ready_expr, "returnByValue": True},
                timeout=remaining_deadline(5.0),
            )
            video_info = eval_res.get("result", {}).get("value", {})
            if isinstance(video_info, dict) and video_info.get("ready"):
                break
            await asyncio.sleep(0.3)

    if not (isinstance(video_info, dict) and video_info.get("found")):
        raise RuntimeError(f"页面未检测到视频播放器: {web_url}")
    raw_dur = video_info.get("duration")
    duration = float(raw_dur) if isinstance(raw_dur, int | float | str) else 0.0
    if duration <= 0:
        raise RuntimeError(f"视频时长无效或流加载超时: {web_url}")

    final_timestamps = _resolve_timestamps(
        duration=duration,
        timestamps=timestamps,
        start=start,
        end=end,
        step=step,
        count=count,
    )

    frames: list[VideoSnapshotFrame] = []
    for idx, ts in enumerate(final_timestamps, 1):
        file_name = f"frame_{idx:02d}_{int(ts):04d}s.jpg" if ts.is_integer() else f"frame_{idx:02d}_{ts:.2f}s.jpg"
        file_path = save_dir / file_name

        capture_script = _get_script("capture.js")
        capture_args = {
            "timestamp": ts,
            "width": width,
            "height": height,
            "quality_ratio": quality_ratio,
        }
        seek_and_draw_js = f"({capture_script})({json.dumps(capture_args, ensure_ascii=False)})"

        try:
            now = asyncio.get_running_loop().time()
            remaining = op_deadline - now
            if remaining <= 0:
                frames.append(VideoSnapshotFrame(timestamp=ts, error="截帧操作已超时 (op_deadline exceeded)"))
                continue
            res = await cdp.call(
                "Runtime.evaluate",
                {"expression": seek_and_draw_js, "awaitPromise": True, "returnByValue": True},
                timeout=min(10.0, remaining),
            )
            data = res.get("result", {}).get("value", {})
            if not isinstance(data, dict):
                raise RuntimeError(f"无效的帧渲染返回: {data}")

            if data.get("ok"):
                data_url = str(data.get("dataUrl", ""))
                raw_b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
                jpeg_bytes = base64.b64decode(raw_b64)
                file_path.write_bytes(jpeg_bytes)
                frames.append(
                    VideoSnapshotFrame(
                        timestamp=ts,
                        file_path=str(file_path),
                        data_url=data_url,
                        width=int(data.get("width", width)),
                        height=int(data.get("height", height)),
                        size_bytes=len(jpeg_bytes),
                    )
                )
            elif data.get("fallback_required"):
                hide_script = _get_script("hide.js")
                hide_dom_js = f"({hide_script})({{}})"
                dom_timeout = min(5.0, max(0.5, op_deadline - asyncio.get_running_loop().time()))
                dom_res = await cdp.call(
                    "Runtime.evaluate",
                    {"expression": hide_dom_js, "returnByValue": True},
                    timeout=dom_timeout,
                )
                rect = dom_res.get("result", {}).get("value")
                if not isinstance(rect, dict):
                    raise RuntimeError("降级截帧失败：无法获取视频元素位置")

                rect_w = float(rect.get("width") or width)
                rect_h = float(rect.get("height") or height)
                scale = round(width / rect_w, 4) if rect_w > 0 else 1.0
                target_w = round(rect_w * scale)
                target_h = round(rect_h * scale)

                clip = {
                    "x": float(rect.get("x", 0)),
                    "y": float(rect.get("y", 0)),
                    "width": rect_w,
                    "height": rect_h,
                    "scale": scale,
                }
                shot_timeout = min(10.0, max(0.5, op_deadline - asyncio.get_running_loop().time()))
                shot_res = await cdp.call(
                    "Page.captureScreenshot",
                    {"format": "jpeg", "quality": quality_int, "clip": clip, "captureBeyondViewport": True},
                    timeout=shot_timeout,
                )
                raw_b64 = shot_res.get("data", "")
                jpeg_bytes = base64.b64decode(raw_b64)
                file_path.write_bytes(jpeg_bytes)
                frames.append(
                    VideoSnapshotFrame(
                        timestamp=ts,
                        file_path=str(file_path),
                        data_url=f"data:image/jpeg;base64,{raw_b64}",
                        width=target_w,
                        height=target_h,
                        size_bytes=len(jpeg_bytes),
                    )
                )
            else:
                frames.append(
                    VideoSnapshotFrame(
                        timestamp=ts,
                        error=str(data.get("error", "unknown_frame_error")),
                    )
                )
        except Exception as exc:
            # If connection is broken or closed, re-raise as transport failure
            # so caller can invalidate dead session and fallback cleanly
            exc_str = str(exc)
            if (
                isinstance(exc, (ConnectionError, ConnectionResetError, TimeoutError))
                or not getattr(cdp, "alive", True)
                or "CDP 客户端连接已关闭" in exc_str
                or "断开连接" in exc_str
            ):
                raise
            logger.warning(f"截取时间戳 {ts}s 失败: {exc}")
            frames.append(VideoSnapshotFrame(timestamp=ts, error=str(exc)))
    return frames


async def capture_video_frames(
    web_url: str,
    cookies: dict[str, str],
    cookie_domain: str,
    timestamps: list[float] | None = None,
    width: int = 1280,
    height: int = 720,
    quality: int = 85,
    output_dir: Path | None = None,
    timeout: float = 30.0,
    *,
    start: float | None = None,
    end: float | None = None,
    step: float | None = None,
    count: int | None = None,
    course_id: str | None = None,
    video_id: str | None = None,
    use_cache: bool = True,
    session_dir: Path | None = None,
    account_id: str | None = None,
    platform: str | None = None,
) -> list[VideoSnapshotFrame]:
    """对网页视频提取指定时间戳的高清纯净帧。"""
    save_dir = Path(output_dir) if output_dir is not None else Path(tempfile.mkdtemp(prefix="wk_snapshot_"))
    save_dir.mkdir(parents=True, exist_ok=True)

    quality_int = max(30, min(95, quality))
    quality_ratio = quality_int / 100.0
    op_deadline = asyncio.get_running_loop().time() + timeout

    if use_cache and course_id and video_id:
        session_file = get_session_file(
            course_id=course_id,
            video_id=video_id,
            session_dir=session_dir,
            account_id=account_id,
            platform=platform,
        )
        warm_session = None
        try:
            async with async_file_lock(session_file, timeout=15.0):
                warm_session, is_new = await get_or_create_warm_session(
                    course_id=course_id,
                    video_id=video_id,
                    web_url=web_url,
                    cookies=cookies,
                    cookie_domain=cookie_domain,
                    session_dir=session_dir,
                    timeout=timeout,
                    account_id=account_id,
                    platform=platform,
                )
                page_ws = get_active_page_ws(warm_session.port, warm_session.page_ws) or warm_session.page_ws
                if not page_ws:
                    raise RuntimeError(f"无法连接到浏览器调试端口 127.0.0.1:{warm_session.port}")

                async with build_session(timeout=15.0, proxy=None, user_agent=None, impersonate=None) as session:
                    ws = await session.ws_connect(page_ws)
                    cdp = CdpClient(ws)
                    try:
                        return await _extract_frames_cdp(
                            cdp=cdp,
                            web_url=web_url,
                            is_new=is_new,
                            timestamps=timestamps,
                            width=width,
                            height=height,
                            quality_int=quality_int,
                            quality_ratio=quality_ratio,
                            save_dir=save_dir,
                            op_deadline=op_deadline,
                            start=start,
                            end=end,
                            step=step,
                            count=count,
                        )
                    finally:
                        await cdp.close()
                        touch_session(session_file)
        except (asyncio.CancelledError, KeyboardInterrupt):
            if warm_session is not None:
                clean_session_resources(warm_session, session_file)
            raise
        except Exception as exc:
            logger.warning(f"热池会话提取失败，将降级至临时浏览器: {exc}")
            if warm_session is not None:
                clean_session_resources(warm_session, session_file)
    async with BrowserManager.acquire_session(mode="headless", cookies=cookies, cookie_domain=cookie_domain) as b_proc:
        page_ws = b_proc.page_ws
        if not page_ws:
            raise RuntimeError(f"无法连接到浏览器调试端口 127.0.0.1:{b_proc.port}")

        async with build_session(timeout=15.0, proxy=None, user_agent=None, impersonate=None) as session:
            ws = await session.ws_connect(page_ws)
            cdp = CdpClient(ws)
            try:
                return await _extract_frames_cdp(
                    cdp=cdp,
                    web_url=web_url,
                    is_new=True,
                    timestamps=timestamps,
                    width=width,
                    height=height,
                    quality_int=quality_int,
                    quality_ratio=quality_ratio,
                    save_dir=save_dir,
                    op_deadline=op_deadline,
                    start=start,
                    end=end,
                    step=step,
                    count=count,
                )
            finally:
                await cdp.close()
