"""通用视频观看逻辑与参数化执行引擎。"""

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, replace
from typing import Any, Protocol

import niquests

from wkhelper.core.events import EventCallback, notifier
from wkhelper.core.json_types import json_float, parse_json_text
from wkhelper.core.plan import VideoParameters, VideoStatus
from wkhelper.core.results import VideoWatchResult
from wkhelper.core.throttle import parse_retry_hint

logger = logging.getLogger(__name__)

type RequestOptions = dict[str, Any]


@dataclass
class VideoWatchOptions:
    """视频学习参数配置。

    心跳节奏对齐 Web 播放器：每 `sample_interval` 秒采一个打点，攒满 `batch_size`
    个后集中上报一次（默认 5s × 6 = 30s 一批），避免高频零散请求形成脚本特征。
    """

    learning_rate: float = 1.0
    sample_interval: float = 5.0
    batch_size: int = 6
    heartbeat_interval: float = 30.0
    jitter: float = 0.2
    progress_interval: float = 60.0
    completion_threshold: float = 1.0
    video_timeout: float = 1800.0
    total_timeout: float = 3600.0
    stall_timeout: float = 180.0
    max_heartbeats: int = 400
    max_retries: int = 2
    retry_backoff: float = 2.0
    video_delay: float = 3.0

    def with_parameters(self, parameters: VideoParameters | None) -> "VideoWatchOptions":
        """返回叠加了显式参数的新配置；未指定字段保持原值。"""
        if parameters is None:
            return self
        return replace(
            self,
            learning_rate=parameters.learning_rate if parameters.learning_rate is not None else self.learning_rate,
            heartbeat_interval=(parameters.heartbeat_interval if parameters.heartbeat_interval is not None else self.heartbeat_interval),
            jitter=parameters.jitter if parameters.jitter is not None else self.jitter,
            progress_interval=(parameters.progress_interval if parameters.progress_interval is not None else self.progress_interval),
            completion_threshold=(
                parameters.completion_threshold if parameters.completion_threshold is not None else self.completion_threshold
            ),
            video_timeout=parameters.video_timeout if parameters.video_timeout is not None else self.video_timeout,
            stall_timeout=parameters.stall_timeout if parameters.stall_timeout is not None else self.stall_timeout,
            max_heartbeats=parameters.max_heartbeats if parameters.max_heartbeats is not None else self.max_heartbeats,
            video_delay=parameters.video_delay if parameters.video_delay is not None else self.video_delay,
        )


class PayloadGenerator(Protocol):
    """生成单个心跳打点的协议。"""

    def __call__(
        self,
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
    ) -> RequestOptions: ...


def parse_progress_detail(text: str, video_id: str) -> tuple[float, float, float]:
    """从进度响应文本解析 (rate, watch_length, video_length)；结构不符时返回零值。"""
    parsed = parse_json_text(text)
    if not isinstance(parsed, dict):
        return (0.0, 0.0, 0.0)
    data = parsed.get("data")
    if not isinstance(data, dict):
        return (0.0, 0.0, 0.0)
    entry = data.get(video_id)
    if not isinstance(entry, dict):
        return (0.0, 0.0, 0.0)
    return (
        json_float(entry.get("rate")),
        json_float(entry.get("watch_length")),
        json_float(entry.get("video_length")),
    )


def parse_progress_text(text: str, video_id: str) -> tuple[float, float]:
    """从进度响应文本解析 (rate, watch_length)。"""
    rate, watch_length, _ = parse_progress_detail(text, video_id)
    return (rate, watch_length)


async def generic_watch_video(
    client: niquests.AsyncSession,
    video_id: str,
    video_name: str,
    classroom_id: str,
    user_id: int,
    course_id: int,
    sku_id: int,
    progress_url: str,
    heartbeat_url: str,
    payload_gen: PayloadGenerator,
    progress_params: dict[str, Any] | None = None,
    request_kwargs: RequestOptions | None = None,
    options: VideoWatchOptions | None = None,
    on_event: EventCallback | None = None,
    media_ccid: str = "",
    video_duration: float = 0.0,
) -> VideoWatchResult:
    """通用视频观看循环：支持完整自定义参数、有界预算、停滞检测与结果复查。"""
    opts = options or VideoWatchOptions()
    loop = asyncio.get_running_loop()
    start_time = loop.time()
    deadline = start_time + opts.video_timeout

    _notify = notifier(on_event)

    def _remaining_time() -> float:
        return max(0.05, deadline - loop.time())

    def _progress_kwargs(limit: float = 10.0) -> RequestOptions:
        """进度查询专用参数：平台 query 参数只对进度接口生效，不得污染心跳。"""
        return {**(request_kwargs or {}), "params": progress_params or {}, "timeout": min(limit, _remaining_time())}

    # 1. 检查初始服务端进度
    initial_rate = 0.0
    video_frame = 0.0
    try:
        resp = await client.get(progress_url, **_progress_kwargs())
        if resp.text:
            if '"completed":1' in resp.text:
                _notify("video.completed", {"video_id": video_id, "name": video_name, "rate": 1.0, "skipped": True})
                return VideoWatchResult(
                    video_id=video_id,
                    video_name=video_name,
                    status="completed",
                    platform_completed=True,
                    initial_rate=1.0,
                    final_rate=1.0,
                    heartbeats_sent=0,
                    duration_seconds=0.0,
                    message="视频此前已全部完成，直接跳过",
                )
            initial_rate, video_frame, reported_length = parse_progress_detail(resp.text, video_id)
            if video_duration <= 0 and reported_length > 0:
                # 平台未直接给出时长时，用服务端记录的 video_length 兜底
                video_duration = reported_length
    except Exception as exc:
        logger.debug(f"获取初始进度异常: {exc}")

    _notify(
        "video.started",
        {"video_id": video_id, "name": video_name, "initial_rate": initial_rate, "threshold": opts.completion_threshold},
    )

    current_rate = initial_rate
    last_progress_change_time = loop.time()
    last_progress_check_time = loop.time()
    heartbeats_sent = 0
    status: VideoStatus = "completed"
    message = "已完成"

    rate_multiplier = float(opts.learning_rate)
    sample_interval = max(0.01, float(opts.sample_interval))
    batch_size = max(1, int(opts.batch_size))
    seq = 1  # 前端 sq 在页面生命周期内从 1 单调自增，跨批次不重置

    while current_rate < opts.completion_threshold:
        now = loop.time()
        # 超时检查
        if now >= deadline:
            status = "timed_out"
            message = f"达到单视频总时间预算 ({opts.video_timeout}s)"
            break

        # 无进展停滞检查
        if (now - last_progress_change_time) >= opts.stall_timeout:
            status = "stalled"
            message = f"服务端进度在 {opts.stall_timeout}s 内无变化，已停止"
            break

        # 最大心跳批次数检查
        if heartbeats_sent >= opts.max_heartbeats:
            status = "timed_out"
            message = f"已达最大心跳请求上限 ({opts.max_heartbeats})"
            break

        # 构造一批打点：时间戳按采样间隔递增，cp 随播放速率累进
        batch_start_ms = int(time.time() * 1000)
        batch: list[RequestOptions] = []
        for offset in range(batch_size):
            _seq = seq + offset
            batch.append(
                payload_gen(
                    video_id=video_id,
                    classroom_id=classroom_id,
                    user_id=user_id,
                    course_id=course_id,
                    sku_id=sku_id,
                    video_frame=round(video_frame + rate_multiplier * sample_interval * offset, 1),
                    seq=_seq,
                    timestamp=batch_start_ms + int(sample_interval * 1000) * offset,
                    cc=media_ccid,
                    duration=video_duration,
                )
            )
        seq += batch_size
        video_frame += rate_multiplier * sample_interval * batch_size

        # 发送心跳（带重试与预算扣除）
        attempt = 0
        while attempt <= opts.max_retries and heartbeats_sent < opts.max_heartbeats and loop.time() < deadline:
            heartbeats_sent += 1
            attempt += 1
            try:
                post_kw = dict(request_kwargs or {})
                post_kw["timeout"] = min(10.0, _remaining_time())
                r = await client.post(heartbeat_url, json={"heart_data": batch}, **post_kw)

                body = r.text or ""
                hint = parse_retry_hint(body, r.headers.get("Retry-After"))
                if hint is not None or r.status_code == 429:
                    remain = max(1, math.ceil(hint if hint is not None else opts.retry_backoff))
                    _notify("video.status", {"video_id": video_id, "status": f"限流等待 {remain}s"})
                    await asyncio.sleep(min(float(remain), _remaining_time()))
                    continue
                # 发送成功退出重试
                break
            except Exception as exc:
                logger.debug(f"心跳发送异常 (尝试 {attempt}/{opts.max_retries + 1}): {exc}")
                if attempt <= opts.max_retries and loop.time() < deadline:
                    backoff = min(opts.retry_backoff * (2 ** (attempt - 1)), _remaining_time())
                    await asyncio.sleep(backoff)

        # 上报间隔（带随机抖动）：采样间隔只影响打点内容，不额外占用墙钟时间
        base_interval = max(0.001, float(opts.heartbeat_interval))
        if opts.jitter > 0:
            delta = base_interval * opts.jitter
            effective_interval = max(0.001, base_interval + random.uniform(-delta, delta))
        else:
            effective_interval = base_interval
        sleep_interval = min(effective_interval, _remaining_time())
        if sleep_interval > 0:
            await asyncio.sleep(sleep_interval)
        # 进度复查周期
        if (loop.time() - last_progress_check_time) >= opts.progress_interval and loop.time() < deadline:
            last_progress_check_time = loop.time()
            try:
                resp = await client.get(progress_url, **_progress_kwargs())
                if resp.text:
                    new_rate, _ = parse_progress_text(resp.text, video_id)
                    if new_rate > current_rate:
                        current_rate = new_rate
                        last_progress_change_time = loop.time()
                    _notify("video.progress", {"video_id": video_id, "name": video_name, "rate": current_rate})
            except Exception as exc:
                logger.debug(f"定期复查进度失败: {exc}")

    # 循环退出后最终向服务端复查一次真实进度（受剩余时间预算约束）
    final_platform_completed = False
    if loop.time() < deadline:
        try:
            resp = await client.get(progress_url, **_progress_kwargs(5.0))
            if resp.text:
                if '"completed":1' in resp.text:
                    final_platform_completed = True
                observed_rate, _ = parse_progress_text(resp.text, video_id)
                if observed_rate > 0:
                    current_rate = observed_rate
        except Exception:
            pass

    duration = loop.time() - start_time

    # 终态状态校准：如果最终平台已完成或已达到设定的阈值，修正状态
    if final_platform_completed or current_rate >= 1.0:
        status = "completed"
        message = "平台已确认全部完成"
    elif current_rate >= opts.completion_threshold:
        status = "threshold_reached"
        message = f"达到设定阈值 ({opts.completion_threshold})"

    _notify(
        "video.finished",
        {
            "video_id": video_id,
            "name": video_name,
            "status": status,
            "final_rate": current_rate,
            "platform_completed": final_platform_completed,
            "duration": duration,
        },
    )

    return VideoWatchResult(
        video_id=video_id,
        video_name=video_name,
        status=status,
        platform_completed=final_platform_completed,
        initial_rate=initial_rate,
        final_rate=current_rate,
        heartbeats_sent=heartbeats_sent,
        duration_seconds=round(duration, 2),
        message=message,
    )
