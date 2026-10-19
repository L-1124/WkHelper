"""考试查询、考场封面检索与进入考场命令。"""

import json
from typing import Annotated, Any

from cyclopts import App, Parameter

from wkhelper.cli.context import PlatformCode, ctx
from wkhelper.cli.deps import _open_course, _paginate
from wkhelper.cli.envelope import CommandResult, NextAction
from wkhelper.core.events import emit_event
from wkhelper.core.exam_session import browser_cookies, close_exam_browser, load_session, open_exam_browser
from wkhelper.core.exceptions import ConfirmationRequiredError
from wkhelper.core.exporter import export_exam_paper_to_markdown
from wkhelper.core.json_types import JsonValue
from wkhelper.core.results import (
    ExamAnswerResult,
    ExamBrowserCloseResult,
    ExamCoverDetail,
    ExamExportResult,
    ExamListData,
    ExamPaperData,
    ExamResultData,
    ExamStartPreview,
    ExamStartResult,
    ExamStatusData,
    ExamSubmitResult,
)

exam_app = App(name="exam", help="考试查询与考场管理")

_CLI_NOTICE = "CLI 经 REST 接口写入，不会产生考场心跳、桌面截屏与切屏上报；如需完整监考记录，请同时保持浏览器考场页面开启"


def proctoring_payload(cover: ExamCoverDetail) -> dict[str, JsonValue] | None:
    """把监考规则收敛为事件载荷；未开启监考时返回 None。"""
    if not (cover.online_proctor or cover.capture_screen or cover.app_capture_screen or cover.screen_cuts):
        return None
    return {
        "exam_id": cover.exam_id,
        "online_proctor": cover.online_proctor,
        "capture_screen": cover.capture_screen,
        "app_capture_screen": cover.app_capture_screen,
        "screen_cuts": cover.screen_cuts,
        "max_screen_cuts": cover.max_screen_cuts,
        "max_retry": cover.max_retry,
        "note": "该考试开启监考：桌面截屏与切屏上报由考场网页执行，CLI 作答不会产生截图与心跳记录",
    }


def _warn_proctoring(cover: ExamCoverDetail) -> None:
    """考试写操作前通过 NDJSON 事件提示监考规则。"""
    payload = proctoring_payload(cover)
    if payload is not None:
        emit_event("security.exam_proctoring", payload)


async def _bind_browser_session(plat: Any, exam: str) -> None:
    """有活跃考场浏览器会话时复用它：注入浏览器 Cookie，并禁止再换票。"""
    session = load_session(ctx.data_dir)
    if session is None or session.exam_id != str(exam):
        return
    cookies = await browser_cookies(session)
    for name, value in cookies.items():
        plat.client.cookies.set(name, value)
    plat.bind_exam_room(session.host, session.eid, session.web_url)


def _ensure_not_overdue(cover: ExamCoverDetail) -> None:
    """已过截止时间（已缺考）的考试不允许再进入或交卷。"""
    from wkhelper.core.exceptions import APIError

    if cover.status == "已缺考":
        raise APIError(f"考试 {cover.exam_id} 已过截止时间（已缺考），无法进入考场或交卷", retryable=False)


@exam_app.command(name="list")
async def exam_list_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
    limit: Annotated[int, Parameter(name=["--limit"], help="每页条数")] = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
) -> CommandResult[ExamListData]:
    """有界分页列出指定课程的期中/期末考试、开考时间与截止时间。"""
    async with _open_course(course, platform, account) as (plat, matched):
        exams = await plat.get_exams(matched)
        page_items, meta = _paginate(exams, limit, cursor)
        return CommandResult(data=ExamListData(exams=page_items), meta=meta.to_dict())


@exam_app.command(name="inspect")
async def exam_inspect_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    exam: Annotated[str, Parameter(name=["--exam", "-e"], help="考试节点 ID")],
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExamCoverDetail]:
    """查看考试封面、考试须知、满分、限时时长与考场状态。"""
    async with _open_course(course, platform, account) as (plat, matched):
        await _bind_browser_session(plat, exam)
        cover = await plat.get_exam_cover(exam, matched)
        room_status = await plat.get_exam_room_status(exam, matched)
        if room_status is not None:
            cover.status = room_status
        if cover.status == "已缺考":
            next_actions = [
                NextAction(
                    reason="已缺考：截止时间已过且未参加考试，本场不可再进入",
                    argv=[],
                    description=f"考试 {exam} 已逾期，`exam start` 与 `exam submit` 均不会成功",
                )
            ]
        elif cover.status == "已交卷":
            next_actions = [
                NextAction(
                    reason="已交卷：本场作答已提交，无需再次进入",
                    argv=[],
                    description="如需查看成绩或试卷解析，请等待平台公布",
                )
            ]
        else:
            next_actions = [
                NextAction(reason="预览考场信息", argv=["wkhelper", "exam", "start", "-c", course, "-e", exam, "--dry-run"]),
                NextAction(reason="进入考场并启动计时", argv=["wkhelper", "exam", "start", "-c", course, "-e", exam, "--confirm"]),
            ]
        return CommandResult[ExamCoverDetail](data=cover, next_actions=next_actions)


@exam_app.command(name="start")
async def exam_start_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    exam: Annotated[str, Parameter(name=["--exam", "-e"], help="考试节点 ID")],
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="校验并预览考场信息，不触发开始考试")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认进入考场会话（不开始作答，需配合 --begin）")] = False,
    browser: Annotated[bool, Parameter(name=["--browser"], help="进入考场后拉起常驻浏览器保持考场页面（心跳与监考采集）")] = False,
    begin: Annotated[bool, Parameter(name=["--begin"], help="调用 start_paper 真正开始作答并启动服务端计时")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExamStartResult] | CommandResult[ExamStartPreview]:
    """建立考场会话并返回考场直达地址；传入 `--begin` 才真正开始作答。"""
    if not confirm and not dry_run:
        raise ConfirmationRequiredError(
            f"进入考场将启动服务端限时倒计时，请传入 `--confirm` 确认进入，或使用 `--dry-run` 预览。\n目标考试 ID: {exam}",
            next_actions=[
                NextAction(
                    reason="确认进入考场并启动考试会话",
                    argv=["wkhelper", "exam", "start", "-c", course, "-e", exam, "--confirm"],
                ),
                NextAction(
                    reason="安全预览考场信息",
                    argv=["wkhelper", "exam", "start", "-c", course, "-e", exam, "--dry-run"],
                ),
            ],
        )

    async with _open_course(course, platform, account) as (plat, matched):
        cover = await plat.get_exam_cover(exam, matched)
        _ensure_not_overdue(cover)
        _warn_proctoring(cover)

        if dry_run:
            preview = ExamStartPreview(
                dry_run=True,
                course_id=str(matched.id),
                exam_id=str(exam),
                title=cover.title,
                limit_minutes=cover.limit_minutes,
                note="预览模式不会向平台申请考试令牌，确认进入考场请传入 `--confirm`",
                notice=_CLI_NOTICE,
            )
            return CommandResult[ExamStartPreview](data=preview)

        result = await plat.start_exam(exam, matched) if not begin else await plat.begin_exam(exam, matched)
        result.notice = _CLI_NOTICE
        if browser:
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(result.web_url)
            eid = int(parse_qs(parsed.query).get("exam_id", ["0"])[0])
            session = await open_exam_browser(
                exam_id=str(exam),
                platform=plat.__class__.__name__,
                host=f"{parsed.scheme}://{parsed.netloc}",
                eid=eid,
                web_url=result.web_url,
                cookies={k: v for k, v in plat.client.cookies.items()},
                data_dir=ctx.data_dir,
            )
            result.browser_port = session.port
            emit_event(
                "exam.browser_opened",
                {"exam_id": str(exam), "port": session.port, "web_url": session.web_url},
            )
        return CommandResult[ExamStartResult](data=result)


@exam_app.command(name="paper")
async def exam_paper_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    exam: Annotated[str, Parameter(name=["--exam", "-e"], help="考试节点 ID")],
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExamPaperData]:
    """读取考场子系统下发的整卷题目（只读，不提交答案）。"""
    async with _open_course(course, platform, account) as (plat, matched):
        await _bind_browser_session(plat, exam)
        paper = await plat.get_exam_paper(exam, matched)
        return CommandResult[ExamPaperData](data=paper)


@exam_app.command(name="export")
async def exam_export_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    exam: Annotated[str, Parameter(name=["--exam", "-e"], help="考试节点 ID")],
    output: Annotated[str, Parameter(name=["--output", "-o"], help="导出 Markdown 文件路径")],
    download_images: Annotated[bool, Parameter(name=["--download-images"], help="下载题干与选项配图到 assets 目录")] = True,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExamExportResult]:
    """把考场试卷导出为 Markdown，可选下载配图，拒绝覆盖已有文件。"""
    async with _open_course(course, platform, account) as (plat, matched):
        await _bind_browser_session(plat, exam)
        result = await export_exam_paper_to_markdown(
            plat,
            matched,
            exam,
            output,
            download_resources=download_images,
        )
        return CommandResult[ExamExportResult](data=result)


@exam_app.command(name="answer")
async def exam_answer_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    exam: Annotated[str, Parameter(name=["--exam", "-e"], help="考试节点 ID")],
    question: Annotated[str, Parameter(name=["--question", "-q"], help="题目 ID")],
    answer: Annotated[str, Parameter(name=["--answer"], help="答案键，多选用逗号分隔")],
    delay: Annotated[
        float,
        Parameter(
            name=["--delay"],
            help="单题作答前思考等待秒数（默认 0.0s；建议依据难度动态指定：客观题 5~15s，多选/计算 15~35s，主观题 45~90s，模拟真人）",
        ),
    ] = 0.0,
    delay_jitter: Annotated[
        float,
        Parameter(
            name=["--delay-jitter"],
            help="思考等待随机浮动秒数（默认 0.0s；实际延迟为 delay ± jitter）",
        ),
    ] = 0.0,
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="只预览将要提交的作答")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认写入服务端作答")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExamAnswerResult]:
    """提交考试单题作答（写入服务端暂存，不改动最终得分）。建议依据题目难易程度动态指定 `--delay` 与 `--delay-jitter` 模拟真人思考。"""
    keys = [part.strip() for part in answer.split(",") if part.strip()]
    if not keys:
        raise ConfirmationRequiredError("答案不能为空，请使用 `--answer A` 或 `--answer A,B`")

    if not confirm and not dry_run:
        raise ConfirmationRequiredError(
            f"作答会写入平台暂存，请传入 `--confirm` 确认，或使用 `--dry-run` 预览。\n目标题目 ID: {question}",
            next_actions=[
                NextAction(
                    reason="确认写入该题作答",
                    argv=["wkhelper", "exam", "answer", "-c", course, "-e", exam, "-q", question, "--answer", ",".join(keys), "--confirm"],
                ),
                NextAction(
                    reason="预览该题作答",
                    argv=["wkhelper", "exam", "answer", "-c", course, "-e", exam, "-q", question, "--answer", ",".join(keys), "--dry-run"],
                ),
            ],
        )

    if dry_run:
        return CommandResult[ExamAnswerResult](
            data=ExamAnswerResult(
                dry_run=True,
                exam_id=str(exam),
                problem_id=str(question),
                answer=keys,
                saved=False,
                message="预览模式不会写入平台",
                notice=_CLI_NOTICE,
            )
        )

    async with _open_course(course, platform, account) as (plat, matched):
        await _bind_browser_session(plat, exam)
        cover = await plat.get_exam_cover(exam, matched)
        _ensure_not_overdue(cover)
        _warn_proctoring(cover)
        if delay > 0:
            import asyncio
            import random

            actual_delay = max(0.05, delay + random.uniform(-delay_jitter, delay_jitter) if delay_jitter > 0 else delay)
            await asyncio.sleep(actual_delay)

        result = await plat.answer_exam_problem(exam, matched, question, keys)
        result.notice = _CLI_NOTICE
        return CommandResult[ExamAnswerResult](data=result)


@exam_app.command(name="submit")
async def exam_submit_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    exam: Annotated[str, Parameter(name=["--exam", "-e"], help="考试节点 ID")],
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="只预览作答进度，不交卷")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认交卷（不可撤销）")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExamSubmitResult]:
    """交卷并封存本次考试成绩，提交后不可撤销。"""
    if not confirm and not dry_run:
        raise ConfirmationRequiredError(
            f"交卷后不可撤销且本场考试仅允许一次作答，请传入 `--confirm` 确认，或使用 `--dry-run` 预览。\n目标考试 ID: {exam}",
            next_actions=[
                NextAction(
                    reason="确认交卷",
                    argv=["wkhelper", "exam", "submit", "-c", course, "-e", exam, "--confirm"],
                ),
                NextAction(
                    reason="预览作答进度",
                    argv=["wkhelper", "exam", "submit", "-c", course, "-e", exam, "--dry-run"],
                ),
            ],
        )

    async with _open_course(course, platform, account) as (plat, matched):
        await _bind_browser_session(plat, exam)
        cover = await plat.get_exam_cover(exam, matched)
        _ensure_not_overdue(cover)
        _warn_proctoring(cover)
        if dry_run:
            answered, all_ids = await plat.get_exam_answer_state(exam, matched)
            unanswered = [pid for pid in all_ids if pid not in set(answered)]
            return CommandResult[ExamSubmitResult](
                data=ExamSubmitResult(
                    dry_run=True,
                    exam_id=str(exam),
                    answered_count=len(answered),
                    unanswered=unanswered,
                    submitted=False,
                    message="预览模式不会交卷",
                    notice=_CLI_NOTICE,
                )
            )

        result = await plat.submit_exam(exam, matched)
        result.notice = _CLI_NOTICE
        session = load_session(ctx.data_dir)
        if result.submitted and session is not None and session.exam_id == str(exam):
            await close_exam_browser(session, data_dir=ctx.data_dir)
            emit_event("exam.browser_closed", {"exam_id": str(exam), "reason": "submit"})
        return CommandResult[ExamSubmitResult](data=result)


@exam_app.command(name="close")
async def exam_close_cmd() -> CommandResult[ExamBrowserCloseResult]:
    """关闭 `exam start --browser` 拉起的考场浏览器会话。"""
    session = load_session(ctx.data_dir)
    if session is None:
        return CommandResult[ExamBrowserCloseResult](
            data=ExamBrowserCloseResult(exam_id="", closed=False, message="没有活跃的考场浏览器会话")
        )
    closed = await close_exam_browser(session, data_dir=ctx.data_dir)
    emit_event("exam.browser_closed", {"exam_id": session.exam_id, "reason": "manual"})
    return CommandResult[ExamBrowserCloseResult](
        data=ExamBrowserCloseResult(
            exam_id=session.exam_id,
            closed=closed,
            message="考场浏览器已关闭" if closed else "会话文件已清理，但浏览器可能已提前退出",
        )
    )


@exam_app.command(name="result")
async def exam_result_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    exam: Annotated[str, Parameter(name=["--exam", "-e"], help="考试节点 ID")],
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExamResultData]:
    """读取考场成绩与作答结果概览（只读）。"""
    async with _open_course(course, platform, account) as (plat, matched):
        await _bind_browser_session(plat, exam)
        raw = await plat.get_exam_result(exam, matched)
        problems = raw.get("problem_results") if isinstance(raw.get("problem_results"), list) else []
        grade = raw.get("grade")
        duration = raw.get("duration")
        return CommandResult[ExamResultData](
            data=ExamResultData(
                exam_id=str(exam),
                grade=float(grade) if isinstance(grade, (int, float)) else None,
                duration=int(duration) if isinstance(duration, (int, float)) else None,
                problem_count=len(problems),
                message=(
                    "成绩待批阅（试卷含主观题，批阅前不返回结果）"
                    if int(raw.get("errcode") or 0) == 10107
                    else f"{raw.get('errmsg') or ''} (errcode: {raw.get('errcode')})".strip()
                ),
            )
        )


@exam_app.command(name="status")
async def exam_status_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    exam: Annotated[str, Parameter(name=["--exam", "-e"], help="考试节点 ID")],
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExamStatusData]:
    """汇总考场 cover/refresh_time/cache_results/problem_results 状态（只读诊断）。"""
    async with _open_course(course, platform, account) as (plat, matched):
        await _bind_browser_session(plat, exam)
        raw = await plat.get_exam_status_snapshot(exam, matched)
        return CommandResult[ExamStatusData](
            data=ExamStatusData(
                exam_id=str(exam),
                room_status=raw.get("room_status"),
                room_status_raw=raw.get("room_status_raw") if isinstance(raw.get("room_status_raw"), int) else None,
                lms_status=raw.get("lms_status") if isinstance(raw.get("lms_status"), int) else None,
                refresh_code=raw.get("refresh_code") if isinstance(raw.get("refresh_code"), int) else None,
                refresh_message=str(raw.get("refresh_message") or ""),
                cached_count=int(raw.get("cached_count") or 0),
                cached_sample=json.dumps(raw.get("cached_sample"), ensure_ascii=False) if raw.get("cached_sample") else "",
                result_code=raw.get("result_code") if isinstance(raw.get("result_code"), int) else None,
                result_message=str(raw.get("result_message") or ""),
            )
        )
