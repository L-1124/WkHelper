"""通用视频观看逻辑。"""

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import niquests

from wkhelper.core.config import HEARTBEAT_INTERVAL, LEARNING_RATE, VIDEO_THRESHOLD

logger = logging.getLogger(__name__)

type VideoProgressCallback = Callable[[str, float], None | Awaitable[None]]
type VideoStatusCallback = Callable[[str, str | None], None | Awaitable[None]]
type VideoCompleteCallback = Callable[[str], None | Awaitable[None]]
type RequestOptions = dict[str, Any]


class PayloadGenerator(Protocol):
    """生成心跳载荷的协议。"""

    def __call__(
        self,
        video_id: str,
        classroom_id: str,
        user_id: int,
        course_id: int,
        sku_id: int,
        video_frame: int,
        i: int,
        timestamp: int,
    ) -> RequestOptions: ...


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
    request_kwargs: RequestOptions | None = None,
    on_progress: VideoProgressCallback | None = None,
    on_complete: VideoCompleteCallback | None = None,
    on_status: VideoStatusCallback | None = None,
) -> None:
    """通用视频观看循环。"""

    async def _maybe_call(result: None | Awaitable[None]) -> None:
        if result is not None:
            await result

    # 检查初始进度
    response = None
    try:
        response = await client.get(progress_url, **(request_kwargs or {}))
        if response.text and '"completed":1' in response.text:
            logger.info(f"⏭️  {video_name} 已完成，跳过")
            if on_complete:
                await _maybe_call(on_complete(video_name))
            return
    except Exception as e:
        logger.warning(f"⚠️  获取初始进度失败: {e}")

    logger.info(f"🎬 开始学习: {video_name}")

    # 初始化状态
    video_frame = 0
    rate = 0.0
    if response and response.text:
        try:
            data = json.loads(response.text)["data"][video_id]
            rate = float(data.get("rate", 0) or 0)
            video_frame = int(data.get("watch_length", 0))
        except Exception:
            pass

    timestamp = int(time.time() * 1000)

    while rate <= VIDEO_THRESHOLD:
        # 生成3条心跳记录
        heart_data = [
            payload_gen(
                video_id=video_id,
                classroom_id=classroom_id,
                user_id=user_id,
                course_id=course_id,
                sku_id=sku_id,
                video_frame=video_frame + LEARNING_RATE * i,
                i=i,
                timestamp=timestamp,
            )
            for i in range(3)
        ]

        # 更新帧计数器
        video_frame += LEARNING_RATE * 3

        # 发送心跳（带重试）
        try:
            r = await client.post(heartbeat_url, json={"heart_data": heart_data}, **(request_kwargs or {}))

            # 处理限流
            if r.text:
                match = re.search(r"Expected available in(.+?)second.", r.text)
                if match:
                    delay_time = float(match.group(1).strip())
                    if on_status:
                        remain = max(1, math.ceil(delay_time))
                        while remain > 0:
                            await _maybe_call(on_status(video_name, f"⚠️ 限流，等待 {remain}s"))
                            await asyncio.sleep(1)
                            remain -= 1
                    else:
                        logger.warning(f"⚠️  服务器限流，需等待 {delay_time} 秒")
                        await asyncio.sleep(delay_time + 0.5)
                    # 重试一次
                    if on_status:
                        await _maybe_call(on_status(video_name, "🔄 重试中..."))
                    else:
                        logger.info("🔄 重新发送请求...")
                    await client.post(
                        heartbeat_url,
                        json={"heart_data": heart_data},
                        **(request_kwargs or {}),
                        timeout=20,
                    )
        except Exception:
            logger.debug("heartbeat failed", exc_info=True)

        # 等待
        await asyncio.sleep(HEARTBEAT_INTERVAL)

        # 检查进度
        try:
            response = await client.get(progress_url, **(request_kwargs or {}))
            if response.text:
                rate = float(json.loads(response.text)["data"][video_id].get("rate", 0) or 0)
            if on_progress:
                await _maybe_call(on_progress(video_name, rate))
                if on_status:
                    await _maybe_call(on_status(video_name, None))
            else:
                logger.info(f"📊 {video_name} 进度: {rate * 100:.1f}%")
        except Exception:
            logger.debug("progress check failed", exc_info=True)

    logger.info(f"✅ {video_name} 完成！")
    if on_status:
        await _maybe_call(on_status(video_name, None))
    if on_complete:
        await _maybe_call(on_complete(video_name))
