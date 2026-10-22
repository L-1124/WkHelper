"""video 业务流程。"""

import sys
from functools import partial

from wkhelper.domain.actions import NextAction
from wkhelper.domain.courses import Course
from wkhelper.domain.errors import RiskControlError
from wkhelper.domain.video import VideoProgress, VideoWatchResult
from wkhelper.infra.events import EventCallback
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.parsing import format_leaf_label, iter_leaves_with_context
from wkhelper.platform.protocol_types import create_page_nonce
from wkhelper.video.engine import VideoWatchOptions, generic_watch_video, parse_progress_completed, parse_progress_text


async def get_videos(platform: BasePlatform, course: Course) -> dict[int, str]:
    """从章节数据构建视频字典。"""
    data = await platform.get_chapter_data(course)
    # 优先使用平台权威的「已完成视频集合」；没有时退回章节进度值。
    authoritative_completed = await platform.get_completed_video_ids(course)
    schedules = {} if authoritative_completed is not None else await platform.get_leaf_schedules(course)
    videos: dict[int, str] = {}
    completed_video_ids: set[int] = set()
    for leaf, chapter_name, section_name in iter_leaves_with_context(data):
        # 8=线下课堂没有媒资与心跳流，只保留视频/音频/直播
        if leaf.get("leaf_type") not in (0, 1, 2) or not leaf.get("is_show", True):
            continue
        leaf_id = leaf.get("id")
        if leaf_id is None:
            continue
        video_id = int(leaf_id)
        is_completed = video_id in authoritative_completed if authoritative_completed is not None else schedules.get(video_id, 0.0) >= 1.0
        if is_completed:
            completed_video_ids.add(video_id)

        label = format_leaf_label(leaf, chapter_name, section_name)
        if is_completed:
            label = f"{label} [已完成]"
        videos[video_id] = label

    course.metadata["completed_video_ids"] = completed_video_ids
    return videos


async def do_video(
    platform: BasePlatform,
    video_id: str,
    video_name: str,
    course: Course,
    options: VideoWatchOptions | None = None,
    on_event: EventCallback | None = None,
) -> VideoWatchResult:
    """观看单个视频。"""
    ctx = await platform.prepare_video_context(video_id, course)
    opts = options or VideoWatchOptions()
    page_nonce = create_page_nonce()
    try:
        return await generic_watch_video(
            client=platform.client,
            video_id=ctx.video_id,
            video_name=video_name,
            classroom_id=ctx.classroom_id,
            user_id=ctx.user_id,
            course_id=ctx.course_id,
            sku_id=ctx.sku_id,
            progress_url=ctx.progress_url,
            heartbeat_url=ctx.heartbeat_url,
            payload_gen=partial(platform.build_heartbeat_payload, page_nonce=page_nonce),
            progress_params=ctx.progress_params,
            request_kwargs=ctx.request_kwargs,
            options=opts,
            on_event=on_event,
            media_ccid=ctx.media_ccid,
            video_duration=ctx.duration,
        )
    except RiskControlError as exc:
        web_url = platform.format_web_task_url(course, video_id, "video")
        next_actions = (
            [NextAction(reason="在浏览器打开视频页面完成滑块人机验证后重试", argv=list(sys.argv), description=web_url or "")]
            if web_url
            else None
        )
        raise RiskControlError(str(exc), web_url=web_url, next_actions=next_actions) from exc


async def get_video_progress(platform: BasePlatform, video_id: str, course: Course) -> VideoProgress:
    """查询单视频服务端进度与完成标识。"""
    vctx = await platform.prepare_video_context(video_id, course)
    resp = await platform.client.get(
        vctx.progress_url,
        params=vctx.progress_params,
        **(vctx.request_kwargs or {}),
    )
    resp.raise_for_status()
    text = resp.text or ""
    rate, _ = parse_progress_text(text, str(video_id))
    return VideoProgress(
        course_id=str(course.id),
        video_id=str(video_id),
        current_rate=rate,
        completed=parse_progress_completed(text, str(video_id)) or rate >= 1.0,
    )
