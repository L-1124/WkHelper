"""视频学习与查询命令。"""

from typing import Annotated, Literal

from cyclopts import App, Parameter

from wkhelper.cli.context import PlatformCode
from wkhelper.cli.deps import _load_plan, _open_course, _open_platform, _paginate
from wkhelper.cli.envelope import CommandResult, NextAction
from wkhelper.core.entities import course_reference, dedupe_courses
from wkhelper.core.exceptions import APIError, ConfirmationRequiredError, PartialFailureError
from wkhelper.core.plan import ExecutionOverrides, ExecutionSettings, VideoParameters, VideoPlan, VideoTarget
from wkhelper.core.results import VideoItem, VideoLearnPreview, VideoListData, VideoPlanSummary, VideoProgress
from wkhelper.core.video import VideoWatchOptions
from wkhelper.core.video_runner import VideoPlanRunner

video_app = App(name="video", help="视频学习与查询")


@video_app.command(name="list")
async def video_list_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    status: Annotated[Literal["pending", "completed", "all"], Parameter(name=["--status"], help="状态过滤")] = "all",
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
    limit: Annotated[int, Parameter(name=["--limit"], help="每页条数")] = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
) -> CommandResult[VideoListData]:
    """查询课程视频列表。"""
    async with _open_course(course, platform, account) as (plat, matched):
        videos = await plat.get_videos(matched)
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
        return CommandResult(data=VideoListData(videos=page_items), meta=meta.to_dict())


@video_app.command(name="inspect")
async def video_inspect_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    video: Annotated[str, Parameter(name=["--video", "-v"], help="视频 ID")],
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[VideoProgress]:
    """查询单视频进度与执行约束。"""
    async with _open_course(course, platform, account) as (plat, matched):
        return CommandResult(data=await plat.get_video_progress(video, matched))


@video_app.command(name="learn")
async def video_learn_cmd(
    course: Annotated[str | None, Parameter(name=["--course", "-c"], help="课程 ID")] = None,
    video: Annotated[list[str] | None, Parameter(name=["--video", "-v"], help="视频 ID（可多次指定）")] = None,
    plan_file: Annotated[str | None, Parameter(name=["--plan-file"], help="批量计划 JSON 文件")] = None,
    concurrency: Annotated[int | None, Parameter(name=["--concurrency"], help="并发任务数")] = None,
    learning_rate: Annotated[float | None, Parameter(name=["--learning-rate"], help="推进速率乘数")] = None,
    heartbeat_interval: Annotated[float | None, Parameter(name=["--heartbeat-interval"], help="心跳间隔（秒）")] = None,
    progress_interval: Annotated[float | None, Parameter(name=["--progress-interval"], help="进度复查间隔（秒）")] = None,
    completion_threshold: Annotated[float | None, Parameter(name=["--completion-threshold"], help="完成进度阈值 (0, 1]")] = None,
    video_timeout: Annotated[float | None, Parameter(name=["--video-timeout"], help="单视频总超时（秒）")] = None,
    total_timeout: Annotated[float | None, Parameter(name=["--total-timeout"], help="批任务总超时（秒）")] = None,
    stall_timeout: Annotated[float | None, Parameter(name=["--stall-timeout"], help="无进展停滞超时（秒）")] = None,
    max_heartbeats: Annotated[int | None, Parameter(name=["--max-heartbeats"], help="单视频最大心跳数")] = None,
    jitter: Annotated[float | None, Parameter(name=["--jitter"], help="心跳间隔随机抖动比例 (0-1)")] = None,
    video_delay: Annotated[float | None, Parameter(name=["--video-delay"], help="多视频切换冷却间隔秒数")] = None,
    on_error: Annotated[Literal["stop", "continue"] | None, Parameter(name=["--on-error"], help="出错策略")] = None,
    checkpoint_file: Annotated[str | None, Parameter(name=["--checkpoint-file"], help="检查点状态文件")] = None,
    resume_from: Annotated[str | None, Parameter(name=["--resume-from"], help="从检查点文件恢复执行")] = None,
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="仅预览计划，不执行写操作")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认执行写操作")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[VideoLearnPreview] | CommandResult[VideoPlanSummary]:
    """执行视频学习流程。"""
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
        targets = [VideoTarget(course_id=str(course), video_id=str(video_id)) for video_id in video_ids]
        base_execution = ExecutionSettings()
        plan_defaults = VideoParameters()
        confirm_argv += ["--course", str(course)]
        for video_id in video_ids:
            confirm_argv += ["--video", str(video_id)]

    cli_parameters = VideoParameters(
        learning_rate=learning_rate,
        heartbeat_interval=heartbeat_interval,
        progress_interval=progress_interval,
        completion_threshold=completion_threshold,
        video_timeout=video_timeout,
        stall_timeout=stall_timeout,
        max_heartbeats=max_heartbeats,
        jitter=jitter,
        video_delay=video_delay,
    )
    cli_parameters.validate("命令行参数")
    effective_execution = ExecutionOverrides(concurrency=concurrency, on_error=on_error, total_timeout=total_timeout).overlay(
        base_execution
    )

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


video_list = video_list_cmd
video_inspect = video_inspect_cmd
video_learn = video_learn_cmd

__all__ = [
    "video_app",
    "video_inspect",
    "video_inspect_cmd",
    "video_learn",
    "video_learn_cmd",
    "video_list",
    "video_list_cmd",
]
