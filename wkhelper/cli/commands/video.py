"""视频学习与查询命令。"""

from typing import Annotated, Literal

from cyclopts import App, Parameter

from wkhelper.cli.context import PlatformCode
from wkhelper.cli.deps import _load_plan, _open_course, _open_platform, _paginate
from wkhelper.cli.envelope import CommandResult
from wkhelper.cli.inputs import Limit, validate_positive_limit, validate_snapshot_input
from wkhelper.domain.actions import NextAction
from wkhelper.domain.courses import course_reference, dedupe_courses
from wkhelper.domain.errors import APIError, ConfirmationRequiredError, PartialFailureError
from wkhelper.domain.validation import validate_input
from wkhelper.domain.video import VideoItem, VideoListData, VideoPlanSummary, VideoProgress, VideoSnapshotData
from wkhelper.video.engine import VideoWatchOptions
from wkhelper.video.plan import ExecutionOverrides, ExecutionSettings, VideoLearnPreview, VideoParameters, VideoPlan, VideoTarget
from wkhelper.video.runner import VideoPlanRunner
from wkhelper.video.service import get_video_progress, get_videos
from wkhelper.video.session_pool import cleanup_all_warm_sessions
from wkhelper.video.snapshot import capture_video_frames

video_app = App(name="video", help="视频查询、播放与抽帧")


@video_app.command(name="list")
async def video_list_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    status: Annotated[Literal["pending", "completed", "all"], Parameter(name=["--status"], help="状态过滤")] = "all",
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
    limit: Limit = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
) -> CommandResult[VideoListData]:
    """查询课程的视频任务列表。"""
    validate_positive_limit(int, limit)
    async with _open_course(course, platform, account) as (plat, matched):
        videos = await get_videos(plat, matched)
        raw_completed = matched.metadata.get("completed_video_ids")
        completed_ids: set[int] = raw_completed if isinstance(raw_completed, set) else set()

        items: list[VideoItem] = []
        for video_id, label in videos.items():
            is_completed = video_id in completed_ids or "[已完成]" in label
            if status == "pending" and is_completed:
                continue
            if status == "completed" and not is_completed:
                continue
            items.append(VideoItem(id=str(video_id), name=label.replace(" [已完成]", ""), completed=is_completed))

        page_items, meta = _paginate(items, limit, cursor)
        return CommandResult(data=VideoListData(videos=page_items), meta=meta.model_dump())


@video_app.command(name="inspect")
async def video_inspect_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    video: Annotated[str, Parameter(name=["--video", "-v"], help="视频 ID")],
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[VideoProgress]:
    """查看单个视频的观看进度与参数。"""
    async with _open_course(course, platform, account) as (plat, matched):
        return CommandResult(data=await get_video_progress(plat, video, matched))


@video_app.command(name="learn")
async def video_learn_cmd(
    course: Annotated[str | None, Parameter(name=["--course", "-c"], help="课程 ID")] = None,
    video: Annotated[list[str] | None, Parameter(name=["--video", "-v"], help="视频 ID (可多次指定)")] = None,
    plan_file: Annotated[str | None, Parameter(name=["--plan-file"], help="批量执行计划 JSON 文件")] = None,
    concurrency: Annotated[int | None, Parameter(name=["--concurrency"], help="并发任务数")] = None,
    learning_rate: Annotated[float | None, Parameter(name=["--learning-rate"], help="播放速率倍数")] = None,
    heartbeat_interval: Annotated[float | None, Parameter(name=["--heartbeat-interval"], help="心跳间隔 (秒)")] = None,
    progress_interval: Annotated[float | None, Parameter(name=["--progress-interval"], help="进度复查间隔 (秒)")] = None,
    completion_threshold: Annotated[
        float | None, Parameter(name=["--completion-threshold"], help="完成进度阈值 (0 到 1 之间的小数)")
    ] = None,
    video_timeout: Annotated[float | None, Parameter(name=["--video-timeout"], help="单视频总超时 (秒)")] = None,
    total_timeout: Annotated[float | None, Parameter(name=["--total-timeout"], help="批任务总超时 (秒)")] = None,
    stall_timeout: Annotated[float | None, Parameter(name=["--stall-timeout"], help="无进展停滞超时（秒）")] = None,
    max_heartbeats: Annotated[int | None, Parameter(name=["--max-heartbeats"], help="单视频最大心跳数")] = None,
    jitter: Annotated[float | None, Parameter(name=["--jitter"], help="心跳间隔随机抖动比例 (0-1)")] = None,
    video_delay: Annotated[float | None, Parameter(name=["--video-delay"], help="多视频切换冷却间隔 (秒)")] = None,
    on_error: Annotated[Literal["stop", "continue"] | None, Parameter(name=["--on-error"], help="出错策略")] = None,
    checkpoint_file: Annotated[str | None, Parameter(name=["--checkpoint-file"], help="检查点状态文件")] = None,
    resume_from: Annotated[str | None, Parameter(name=["--resume-from"], help="从检查点文件恢复执行")] = None,
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="仅预览计划，不执行写操作")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认执行写操作")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[VideoLearnPreview] | CommandResult[VideoPlanSummary]:
    """执行视频学习任务。"""
    plan: VideoPlan | None = None
    effective_platform: str | None = platform
    confirm_argv: list[str] = ["wkhelper", "video", "learn"]

    if plan_file is not None:
        if course is not None or video is not None:
            raise ValueError("`--plan-file` 与 `--course` / `--video` 互斥")
        plan = _load_plan(plan_file)
        if plan.platform is not None and platform is not None and plan.platform != platform:
            raise ValueError(f"计划平台 '{plan.platform}' 与命令行 `--platform {platform}` 冲突")
        if plan.account is not None and account is not None and plan.account != account:
            raise ValueError(f"计划账号 '{plan.account}' 与命令行 `--account {account}` 冲突")
        effective_platform = plan.platform if platform is None else platform
        account = account if account is not None else plan.account
        targets = plan.targets
        base_execution = plan.execution
        plan_defaults = plan.video_defaults
        confirm_argv += ["--plan-file", plan_file]
    else:
        if course is None:
            raise ValueError("必须通过 `--course` 与 `--video`，或通过 `--plan-file` 指定学习目标")
        video_ids = video or []
        if not video_ids:
            raise ValueError("必须通过 `--course` 与 `--video`，或通过 `--plan-file` 指定学习目标")
        targets = [validate_input(VideoTarget, {"course_id": course, "video_id": video_id}) for video_id in video_ids]
        base_execution = ExecutionSettings()
        plan_defaults = VideoParameters()
        confirm_argv += ["--course", course]
        for video_id in video_ids:
            confirm_argv += ["--video", video_id]

    cli_parameters = validate_input(
        VideoParameters,
        {
            "learning_rate": learning_rate,
            "heartbeat_interval": heartbeat_interval,
            "progress_interval": progress_interval,
            "completion_threshold": completion_threshold,
            "video_timeout": video_timeout,
            "stall_timeout": stall_timeout,
            "max_heartbeats": max_heartbeats,
            "jitter": jitter,
            "video_delay": video_delay,
        },
    )
    effective_execution = validate_input(
        ExecutionOverrides,
        {
            "concurrency": concurrency,
            "on_error": on_error,
            "total_timeout": total_timeout,
        },
    ).overlay(base_execution)

    if dry_run:
        return CommandResult[VideoLearnPreview](
            data=VideoLearnPreview(
                dry_run=True,
                targets_count=len(targets),
                targets=targets,
                requested_config=base_execution,
                effective_config=effective_execution,
                note="预览模式不产生网络心跳写入，确认执行请传入 `--confirm`",
            )
        )

    if not confirm:
        raise ConfirmationRequiredError(
            message="视频学习操作涉及向平台提交心跳，必须附加 `--confirm` 参数",
            next_actions=[NextAction(reason="确认执行视频学习", argv=[*confirm_argv, "--confirm"])],
        )

    async with _open_platform(effective_platform, account) as plat:
        # 同一课程可能挂在多个课堂下，按课堂级引用建索引，避免空课堂顶掉有内容的课堂
        courses_map = {course_reference(course_item): course_item for course_item in dedupe_courses(await plat.get_courses())}
        runner = VideoPlanRunner(plat)
        summary = await runner.execute_video_plan(
            courses_map=courses_map,
            targets=targets,
            concurrency=effective_execution.concurrency,
            on_error=effective_execution.on_error,
            default_options=VideoWatchOptions(total_timeout=effective_execution.total_timeout),
            plan_parameters=plan_defaults,
            cli_parameters=cli_parameters,
            checkpoint_file=checkpoint_file,
            resume_from=resume_from,
        )
        if summary.failed > 0:
            if summary.completed == 0:
                raise APIError("全部视频任务执行失败")
            raise PartialFailureError("部分视频任务执行失败", data=summary)
        return CommandResult[VideoPlanSummary](data=summary)


@video_app.command(name="snapshot")
async def video_snapshot_cmd(
    course: Annotated[str | None, Parameter(name=["--course", "-c"], help="课程 ID")] = None,
    video: Annotated[str | None, Parameter(name=["--video", "-v"], help="视频 ID")] = None,
    timestamps: Annotated[str | None, Parameter(name=["--timestamps"], help="指定抽帧时间戳 (秒，支持逗号分隔如 '30,60,90')")] = None,
    start: Annotated[float | None, Parameter(name=["--start"], help="采样起始时间 (秒)")] = None,
    end: Annotated[float | None, Parameter(name=["--end"], help="采样结束时间 (秒)")] = None,
    step: Annotated[float | None, Parameter(name=["--step"], help="采样步长 (秒)")] = None,
    count: Annotated[int | None, Parameter(name=["--count"], help="均匀抽帧数量 (上限 30 张)")] = None,
    width: Annotated[int, Parameter(name=["--width"], help="图像宽度 (像素，默认 1280)")] = 1280,
    quality: Annotated[int, Parameter(name=["--quality"], help="JPEG 压缩质量 (30~95，默认 85)")] = 85,
    output_dir: Annotated[str | None, Parameter(name=["--output-dir", "-o"], help="截图保存目录 (缺省保存在临时目录)")] = None,
    no_cache: Annotated[bool, Parameter(name=["--no-cache"], negative="", help="禁用会话缓存复用 (每次冷启动并在用完后立即销毁)")] = False,
    clean_cache: Annotated[bool, Parameter(name=["--clean-cache"], help="清理所有后台保留的视频热会话进程并退出")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[VideoSnapshotData]:
    """从视频中截取画面帧。"""
    if clean_cache:
        cleaned_count = cleanup_all_warm_sessions()
        return CommandResult(
            data=VideoSnapshotData(
                course_id=course or "",
                video_id=video or "",
                total_frames=0,
                frames=[],
            ),
            meta={"cleaned_sessions": cleaned_count, "message": f"成功清理 {cleaned_count} 个后台热会话"},
        )

    snapshot_input = validate_snapshot_input(
        course=course,
        video=video,
        timestamps=timestamps,
        start=start,
        end=end,
        step=step,
        count=count,
        width=width,
        quality=quality,
        output_dir=output_dir,
    )
    assert snapshot_input.course is not None and snapshot_input.video is not None

    async with _open_course(snapshot_input.course, platform, account) as (plat, matched):
        web_url = plat.format_web_task_url(matched, snapshot_input.video, "video")
        if not web_url:
            raise ValueError(f"无法生成视频播放页面 URL: course={snapshot_input.course}, video={snapshot_input.video}")

        cookie_domain = getattr(plat, "cookie_domain", "") or (".xuetangx.com" if "xuetangx" in plat.api_base else ".yuketang.cn")
        client_cookies = getattr(plat.client, "cookies", None)
        cookies = {k: v for k, v in client_cookies.items()} if client_cookies is not None else {}

        frames = await capture_video_frames(
            web_url=web_url,
            cookies=cookies,
            cookie_domain=cookie_domain,
            timestamps=snapshot_input.parsed_timestamps,
            width=snapshot_input.width,
            height=int(snapshot_input.width * 9 / 16) if snapshot_input.width else 720,
            quality=snapshot_input.effective_quality,
            output_dir=snapshot_input.target_output_dir,
            start=snapshot_input.start,
            end=snapshot_input.end,
            step=snapshot_input.step,
            count=snapshot_input.effective_count,
            course_id=snapshot_input.course,
            video_id=snapshot_input.video,
            use_cache=not no_cache,
            account_id=str(plat.user.id) if plat.user is not None else plat.account_id,
            platform=plat.platform_code or matched.platform_id,
        )

        data = VideoSnapshotData(
            course_id=snapshot_input.course,
            video_id=snapshot_input.video,
            total_frames=len(frames),
            frames=frames,
        )

        failed_count = sum(1 for f in frames if f.error is not None)
        if failed_count == len(frames) and len(frames) > 0:
            raise APIError(f"所有视频帧均截取失败 (共 {len(frames)} 帧)")
        if failed_count > 0:
            raise PartialFailureError(f"部分视频帧截取失败 ({failed_count}/{len(frames)} 失败)", data=data)

        return CommandResult(data=data)


video_list = video_list_cmd
video_inspect = video_inspect_cmd
video_learn = video_learn_cmd
video_snapshot = video_snapshot_cmd

__all__ = [
    "video_app",
    "video_inspect",
    "video_inspect_cmd",
    "video_learn",
    "video_learn_cmd",
    "video_list",
    "video_list_cmd",
    "video_snapshot",
    "video_snapshot_cmd",
]
