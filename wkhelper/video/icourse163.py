"""MOOC 双路协议计时；查看标记与持久化观看进度独立。"""

import asyncio
import math

from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError, AuthError, RiskControlError
from wkhelper.domain.video import VideoStatus, VideoWatchResult
from wkhelper.infra.events import EventCallback, notifier
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.protocol_types import MoocVideoContext
from wkhelper.video.engine import VideoWatchOptions


async def watch_mooc_video(
    platform: BasePlatform,
    context: MoocVideoContext,
    video_name: str,
    course: Course,
    options: VideoWatchOptions,
    on_event: EventCallback | None = None,
) -> VideoWatchResult:
    """按真实经过时长上报，成功必须由平台比例和官方断点共同确认。"""
    if options.learning_rate != 1 or options.heartbeat_interval != 10 or options.jitter != 0:
        raise ValueError("MOOC 要求 learning_rate=1、heartbeat_interval=10、jitter=0")
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + options.video_timeout
    notify = notifier(on_event)
    initial_rate = rate = 0.0
    persisted = context.start
    position = context.start
    heartbeats = reports = index = 0
    last_position: int | None = None
    status: VideoStatus = "failed"
    message = "尚未确认平台观看进度"
    last_change = started

    def remaining() -> float:
        budget = deadline - loop.time()
        if budget <= 0:
            raise TimeoutError
        return budget

    def confirmed(threshold: float) -> bool:
        return rate >= threshold and persisted / context.duration >= threshold

    async def refresh(*, resume: bool) -> None:
        nonlocal rate, persisted, last_change
        old_rate, old_resume = rate, persisted
        schedules = await platform.get_leaf_schedules(course)
        value = schedules.get(int(context.unit_id))
        if value is None or not math.isfinite(value) or not 0 <= value <= 1:
            raise APIError("MOOC 未返回有效的目标视频进度", retryable=False)
        if resume:
            latest = await platform.prepare_video_context(context.unit_id, course)
            if not isinstance(latest, MoocVideoContext):
                raise APIError("MOOC 续播上下文类型错误", retryable=False)
            new_resume = latest.start
        else:
            new_resume = persisted
        rate, persisted = value, new_resume
        if rate > old_rate or persisted > old_resume:
            last_change = loop.time()
        notify("video.progress", {"video_id": context.unit_id, "rate": rate, "resume_position": persisted})

    async def check_progress(*, resume: bool) -> None:
        try:
            await refresh(resume=resume)
        except (AuthError, RiskControlError):
            raise
        except Exception as exc:
            notify("video.status", {"video_id": context.unit_id, "message": f"复查进度失败: {exc}"})

    async def report(elapsed: float, *, finished: bool) -> None:
        nonlocal position, reports, index, last_position
        index += 1
        advance = min(60.0, elapsed, context.duration - position)
        target = position + advance
        video_time = int(target)
        if video_time == last_position:
            return
        timeout = remaining()
        reports += 1
        await platform.report_video_learning(
            context,
            video_time=video_time,
            learned_seconds=int(advance),
            finished=finished,
            index=index,
            timeout=timeout,
        )
        position = target
        last_position = video_time

    try:
        async with asyncio.timeout_at(deadline):
            await refresh(resume=False)
            initial_rate = rate
            notify(
                "video.started",
                {"video_id": context.unit_id, "name": video_name, "initial_rate": rate, "threshold": options.completion_threshold},
            )
            if not confirmed(options.completion_threshold):
                if context.unit_view_status in (None, 0):
                    timeout = remaining()
                    reports += 1
                    await platform.record_video_visit(context, timeout=timeout)
                segment_start = loop.time()
                next_vod = segment_start + 10
                next_report = segment_start + 60
                next_query = segment_start + options.progress_interval
                threshold_flushed = position >= context.duration * options.completion_threshold
                at_end = position >= context.duration
                while not confirmed(options.completion_threshold):
                    now = loop.time()
                    if now >= deadline:
                        raise TimeoutError
                    if now - last_change >= options.stall_timeout:
                        status, message = "stalled", "平台比例与持久化断点停滞"
                        break
                    if heartbeats >= options.max_heartbeats:
                        status, message = "stopped", "达到 VOD 心跳次数上限，停止全部写入"
                        await check_progress(resume=True)
                        break
                    if at_end:
                        wake = min(next_query, deadline, last_change + options.stall_timeout)
                    else:
                        until_end = max(0.0, context.duration - position)
                        until_threshold = (
                            max(0.0, context.duration * options.completion_threshold - position) if not threshold_flushed else math.inf
                        )
                        wake = min(
                            next_vod,
                            next_report,
                            next_query,
                            segment_start + until_end,
                            segment_start + until_threshold,
                            deadline,
                            last_change + options.stall_timeout,
                        )
                    await asyncio.sleep(max(0.0, wake - now))
                    now = loop.time()
                    if now >= deadline or now - last_change >= options.stall_timeout:
                        continue
                    elapsed = max(0.0, now - segment_start)
                    reaches_end = not at_end and elapsed >= context.duration - position
                    reaches_threshold = (
                        not at_end and not threshold_flushed and elapsed >= context.duration * options.completion_threshold - position
                    )
                    # 末尾优先；非末尾重合时先发 VOD，两个时钟互不替代。
                    if not at_end and not reaches_end and now >= next_vod:
                        timeout = remaining()
                        heartbeats += 1
                        try:
                            await platform.send_video_heartbeat(context, timeout=timeout)
                        except (AuthError, RiskControlError):
                            raise
                        except Exception as exc:
                            notify("video.status", {"video_id": context.unit_id, "message": f"VOD 心跳失败: {exc}"})
                        next_vod = loop.time() + 10
                        if heartbeats >= options.max_heartbeats:
                            continue
                    if not at_end and (reaches_end or reaches_threshold or now >= next_report):
                        # 延迟后每条最多记 60 秒，不能把调度延迟一次虚报为观看时长。
                        end_report = reaches_end and context.duration - position <= 60
                        await report(elapsed, finished=end_report)
                        segment_start = loop.time()
                        next_report = segment_start + 60
                        threshold_flushed = position >= context.duration * options.completion_threshold
                        at_end = position >= context.duration
                        await check_progress(resume=True)
                        next_query = loop.time() + options.progress_interval
                    elif now >= next_query:
                        await check_progress(resume=True)
                        next_query = loop.time() + options.progress_interval
                if (
                    confirmed(options.completion_threshold)
                    and not at_end
                    and heartbeats < options.max_heartbeats
                    and loop.time() < deadline
                    and int(loop.time() - segment_start) > 0
                ):
                    await report(loop.time() - segment_start, finished=False)
                    await check_progress(resume=True)
                if confirmed(options.completion_threshold):
                    status = "completed" if confirmed(1.0) else "threshold_reached"
                    message = "平台比例与官方持久化断点均已确认"
            else:
                status = "completed" if confirmed(1.0) else "threshold_reached"
                message = "平台比例与官方断点已满足目标，跳过写入"
    except TimeoutError:
        status, message = "timed_out", "达到单视频时间预算"
    except (AuthError, RiskControlError):
        raise
    except Exception as exc:
        status, message = "failed", f"MOOC 学习写入或初始查询失败: {exc}"
        if loop.time() < deadline:
            try:
                async with asyncio.timeout_at(deadline):
                    await check_progress(resume=True)
            except TimeoutError:
                pass
    result = VideoWatchResult(
        video_id=context.unit_id,
        video_name=video_name,
        status=status,
        platform_completed=confirmed(1.0),
        initial_rate=initial_rate,
        final_rate=rate,
        heartbeats_sent=heartbeats,
        learning_reports_sent=reports,
        duration_seconds=loop.time() - started,
        message=message,
    )
    notify("video.finished", result.model_dump(mode="json"))
    return result
