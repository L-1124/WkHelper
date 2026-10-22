"""考试查询、考场封面检索与进入考场命令。"""

import asyncio
import json
import logging
import random
from typing import Annotated, Any

from cyclopts import App, Parameter

from wkhelper.cli.context import PlatformCode, ctx
from wkhelper.cli.deps import _open_course, _paginate
from wkhelper.cli.envelope import CommandResult
from wkhelper.domain.actions import NextAction
from wkhelper.domain.errors import APIError, ConfirmationRequiredError
from wkhelper.domain.exam import (
    ExamAnswerResult,
    ExamBrowserCloseResult,
    ExamCoverDetail,
    ExamExportResult,
    ExamListData,
    ExamPaperData,
    ExamProblemItem,
    ExamResultData,
    ExamStartPreview,
    ExamStartResult,
    ExamStatusData,
)
from wkhelper.domain.json_types import JsonValue
from wkhelper.exam.browser import (
    begin_browser_exam,
    connect_exam_page,
    simulate_browser_answer,
    wait_for_answer_submission,
)
from wkhelper.exam.export import export_exam_paper_to_markdown
from wkhelper.exam.proctoring import assess_proctoring_risk, ensure_exam_operation_allowed
from wkhelper.exam.service import get_exams
from wkhelper.exam.session import browser_cookies, close_exam_browser, load_session, open_exam_browser
from wkhelper.infra.events import emit_event

logger = logging.getLogger(__name__)

exam_app = App(name="exam", help="考试查询与考场管理")

_CLI_NOTICE = "考场作答依托前台真实浏览器模拟点击完成；交卷属于不可逆高危操作，请在浏览器中核对无误后由本人点击交卷"


def proctoring_payload(cover: ExamCoverDetail) -> dict[str, JsonValue] | None:
    """把监考规则收敛为事件载荷；未真正启用监考时返回 None。"""
    is_restricted, reasons = assess_proctoring_risk(cover)
    if not is_restricted:
        return None
    return {
        "exam_id": cover.exam_id,
        "reasons": reasons,
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
    """有活跃考场浏览器会话时复用它：注入浏览器 Cookie 并绑定会话。"""
    session = load_session(ctx.data_dir)
    if session is None or session.exam_id != str(exam):
        return
    cookies = await browser_cookies(session)
    for name, value in cookies.items():
        plat.client.cookies.set(name, value)
    plat.bind_exam_room(session.host, session.eid, session.web_url)


def _ensure_not_overdue(cover: ExamCoverDetail) -> None:
    """已缺考的考试没有可进入的考场会话，只读查询仍然可用。"""
    if cover.status == "已缺考":
        raise APIError(f"考试 {cover.exam_id} 已过截止时间（已缺考），无法进入考场或读取试卷", retryable=False)


def _ensure_not_submitted(cover: ExamCoverDetail) -> None:
    """已交卷的考试不接受任何作答或重新入场。"""
    if cover.status == "已交卷":
        raise APIError(f"考试 {cover.exam_id} 已交卷，无法再次进入考场或作答", retryable=False)


def _normalize_answer(keys: list[str], mode: str) -> list[str]:
    """把作答归一到服务端暂存格式：客观题按大写选项键排序，主观题保留原文。"""
    if mode == "click":
        return sorted({key.strip().upper() for key in keys if key.strip()})
    return [key.strip() for key in keys]


def _answer_plan(problem: ExamProblemItem, raw_answer: str) -> tuple[list[str], str]:
    """按题型决定作答方式：客观题映射到选项键并点击，填空题按空拆分，其余整段输入。"""
    if problem.options:
        lookup: dict[str, str] = {}
        for option in problem.options:
            lookup[option.key.strip().lower()] = option.key
            lookup[option.text.strip().lower()] = option.key
        parts = [part.strip() for part in raw_answer.split(",") if part.strip()]
        return [lookup.get(part.lower(), part) for part in parts], "click"
    if problem.type and "填空" in problem.type:
        return [part.strip() for part in raw_answer.split(",") if part.strip()], "text"
    return [raw_answer], "text"


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
        exams = await get_exams(plat, matched)
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
        is_restricted, reasons = assess_proctoring_risk(cover)
        if is_restricted:
            reasons_text = "、".join(reasons)
            cover.description = (
                f"【强监考安全锁定】本考试已启用强监考（{reasons_text}）。"
                "为保护学籍与诚信记录，自动化进入考场与作答已被锁定；"
                "请在官方客户端或考场网页中由本人独立完成。\n\n" + (cover.description or "")
            )
            next_actions = [
                NextAction(
                    reason="在官方客户端或考场网页中由本人独立完成",
                    argv=[],
                    description="WkHelper 不提供该考试的自动化入口",
                )
            ]
        elif cover.status == "已缺考":
            next_actions = [
                NextAction(
                    reason="已缺考：截止时间已过且未参加考试，本场不可再进入",
                    argv=[],
                    description=f"考试 {exam} 已逾期，`exam start` 无法进入",
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
                NextAction(reason="进入考场并拉起浏览器", argv=["wkhelper", "exam", "start", "-c", course, "-e", exam, "--confirm"]),
            ]
        return CommandResult[ExamCoverDetail](data=cover, next_actions=next_actions)


@exam_app.command(name="start")
async def exam_start_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    exam: Annotated[str, Parameter(name=["--exam", "-e"], help="考试节点 ID")],
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="校验并预览考场信息，不触发开始考试")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认打开前台考场浏览器（不开始答题，答题需配合 --begin）")] = False,
    begin: Annotated[bool, Parameter(name=["--begin"], help="在浏览器中点击开始答题，核验网页响应并验证进入答题页")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExamStartResult] | CommandResult[ExamStartPreview]:
    """打开前台考场浏览器；传入 `--begin` 在浏览器中点击开始答题并验证进入答题页。"""
    if not confirm and not dry_run:
        raise ConfirmationRequiredError(
            f"进入考场需传入 `--confirm`；传入 `--begin` 会在浏览器中点击开始答题。\n目标考试 ID: {exam}",
            next_actions=[
                NextAction(
                    reason="确认进入考场并打开考场浏览器",
                    argv=["wkhelper", "exam", "start", "-c", course, "-e", exam, "--confirm"],
                ),
                NextAction(
                    reason="安全预览考场信息",
                    argv=["wkhelper", "exam", "start", "-c", course, "-e", exam, "--dry-run"],
                ),
            ],
        )

    async with _open_course(course, platform, account) as (plat, matched):
        await _bind_browser_session(plat, exam)
        cover = await plat.get_exam_cover(exam, matched)
        _ensure_not_overdue(cover)
        _ensure_not_submitted(cover)
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

        ensure_exam_operation_allowed(cover)

        result = await plat.start_exam(exam, matched)
        result.notice = _CLI_NOTICE
        if not result.started:
            raise APIError(f"进入考场未获平台确认: {result.message or '平台未确认开始'}", retryable=False)
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
        if begin:
            await begin_browser_exam(session)
            result.message = "已在浏览器中点击开始答题，服务端计时已启动"
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
        cover = await plat.get_exam_cover(exam, matched)
        _ensure_not_overdue(cover)
        ensure_exam_operation_allowed(cover)
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
        cover = await plat.get_exam_cover(exam, matched)
        _ensure_not_overdue(cover)
        ensure_exam_operation_allowed(cover)
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
    """在考场浏览器页面中模拟点击作答（由前端页面自动暂存）。"""
    raw_answer = answer.strip()
    if not raw_answer:
        raise ConfirmationRequiredError("答案不能为空，请使用 `--answer A` 或 `--answer A,B`")

    if not confirm and not dry_run:
        raise ConfirmationRequiredError(
            f"作答将在浏览器中模拟点击并写入平台暂存，请传入 `--confirm` 确认，或使用 `--dry-run` 预览。\n目标题目 ID: {question}",
            next_actions=[
                NextAction(
                    reason="确认模拟点击作答",
                    argv=["wkhelper", "exam", "answer", "-c", course, "-e", exam, "-q", question, "--answer", raw_answer, "--confirm"],
                ),
                NextAction(
                    reason="预览该题作答",
                    argv=["wkhelper", "exam", "answer", "-c", course, "-e", exam, "-q", question, "--answer", raw_answer, "--dry-run"],
                ),
            ],
        )

    if dry_run:
        return CommandResult[ExamAnswerResult](
            data=ExamAnswerResult(
                dry_run=True,
                exam_id=str(exam),
                problem_id=str(question),
                answer=[raw_answer],
                saved=False,
                message="预览模式不会向浏览器或平台写入；作答方式在确认后依据试卷题型判定",
                notice=_CLI_NOTICE,
            )
        )

    async with _open_course(course, platform, account) as (plat, matched):
        await _bind_browser_session(plat, exam)
        cover = await plat.get_exam_cover(exam, matched)
        _ensure_not_overdue(cover)
        _ensure_not_submitted(cover)

        # 考场安全门禁：所有作答必须依托已打开的真实浏览器会话
        session = load_session(ctx.data_dir)
        if session is None or session.exam_id != str(exam):
            raise APIError(
                f"考场作答必须依托已打开的前台考场浏览器会话。\n"
                f"请先运行：wkhelper exam start -c {course} -e {exam} --confirm\n"
                "打开考场浏览器窗口并在前台保持打开后重试。",
                retryable=False,
            )

        # 强监考考试硬阻断
        ensure_exam_operation_allowed(cover)
        _warn_proctoring(cover)

        paper = await plat.get_exam_paper(exam, matched)
        problem = next((item for item in paper.problems if item.id == str(question)), None)
        if problem is None:
            raise APIError(f"考场试卷中不存在题目 {question}，未执行任何作答", retryable=False)

        # 客观题映射到选项键并点击；填空题按空拆分；主观题整段输入
        keys, mode = _answer_plan(problem, raw_answer)
        if not keys:
            raise ConfirmationRequiredError("答案不能为空，请使用 `--answer A` 或 `--answer A,B`")

        if delay > 0:
            actual_delay = max(0.05, delay + random.uniform(-delay_jitter, delay_jitter) if delay_jitter > 0 else delay)
            await asyncio.sleep(actual_delay)

        # 通过 CDP 向考场前台浏览器页面派发模拟 DOM 点击/输入
        cdp = await connect_exam_page(session)
        try:
            # 先记事件基线：页面可能在点击回调里立即发请求，晚记录会漏掉
            baseline = len(cdp.events)
            sim_res = await simulate_browser_answer(cdp, problem.id, keys, mode=mode)
            if not sim_res.get("success"):
                err_msg = sim_res.get("error") or "未能成功定位选项或输入内容"
                raise APIError(f"考场页面模拟作答失败: {err_msg}", retryable=False)
            # 优先用页面自身发出的作答请求作证，避免每题额外回读
            submission = await wait_for_answer_submission(cdp, problem.id, since=baseline)
        finally:
            await cdp.close()

        expected = _normalize_answer(keys, mode)
        observed = bool(submission.get("observed"))
        submitted = _normalize_answer(submission.get("answer") or [], mode) if observed else None
        accepted = bool(submission.get("accepted"))
        rejected = bool(submission.get("rejected"))

        if observed and accepted and submitted == expected:
            return CommandResult[ExamAnswerResult](
                data=ExamAnswerResult(
                    dry_run=False,
                    exam_id=str(exam),
                    problem_id=str(problem.id),
                    answer=keys,
                    saved=True,
                    message="考场页面已提交本题作答且平台返回成功。交卷请在浏览器核对无误后由本人点击交卷。",
                    notice=_CLI_NOTICE,
                )
            )

        if observed and (submitted != expected or rejected):
            reason = f"提交内容为 {submitted or '空'}" if submitted != expected else "平台未返回成功"
            return CommandResult[ExamAnswerResult](
                data=ExamAnswerResult(
                    dry_run=False,
                    exam_id=str(exam),
                    problem_id=str(problem.id),
                    answer=keys,
                    saved=False,
                    message=f"考场页面已发出作答请求，但{reason}，与预期 {expected} 不符。请在浏览器中核对。",
                    notice=_CLI_NOTICE,
                )
            )

        # 未捕获到页面请求，或响应为未知状态且提交内容与预期一致时，回读一次暂存作答兜底
        try:
            cached = await plat.get_exam_cached_answers(exam, matched)
        except (APIError, NotImplementedError) as exc:
            logger.debug(f"读取考场暂存作答失败: {exc}")
            cached = None

        if cached is None:
            saved = False
            message = (
                "已在考场浏览器页面中完成模拟点击/输入并发出作答请求，但响应状态未知且无法读取服务端暂存作答；请在浏览器中确认页面状态。"
                if observed
                else "已在考场浏览器页面中完成模拟点击/输入，但既未捕获到页面提交请求，也无法读取服务端暂存作答；请在浏览器中确认页面状态。"
            )
        elif _normalize_answer(cached.get(str(problem.id)) or [], mode) == expected:
            saved = True
            message = "已在考场浏览器页面完成模拟点击/输入，服务端暂存作答与预期一致。交卷请在浏览器核对无误后由本人点击交卷。"
        else:
            saved = False
            actual = cached.get(str(problem.id))
            message = f"页面已执行模拟点击/输入，但服务端暂存作答与预期不一致（暂存: {actual or '空'}）。请在浏览器中核对。"
        result = ExamAnswerResult(
            dry_run=False,
            exam_id=str(exam),
            problem_id=str(problem.id),
            answer=keys,
            saved=saved,
            message=message,
            notice=_CLI_NOTICE,
        )
        return CommandResult[ExamAnswerResult](data=result)


@exam_app.command(name="close")
async def exam_close_cmd() -> CommandResult[ExamBrowserCloseResult]:
    """关闭 `exam start` 拉起的考场浏览器会话。"""
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
        message = f"{raw.get('errmsg') or ''} (errcode: {raw.get('errcode')})".strip()
        if int(raw.get("errcode") or 0) == 10107:
            message = "试卷进度异常（平台返回 10107），建议联系任课老师核对作答与提交记录"
        return CommandResult[ExamResultData](
            data=ExamResultData(
                exam_id=str(exam),
                grade=float(grade) if isinstance(grade, (int, float)) else None,
                duration=int(duration) if isinstance(duration, (int, float)) else None,
                problem_count=len(problems),
                message=message,
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
