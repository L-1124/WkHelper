"""考试查询、考场封面检索与进入考场命令。"""

from typing import Annotated

from cyclopts import App, Parameter

from wkhelper.cli.context import PlatformCode
from wkhelper.cli.deps import _open_course, _paginate
from wkhelper.cli.envelope import CommandResult, NextAction
from wkhelper.core.exceptions import ConfirmationRequiredError
from wkhelper.core.exporter import export_exam_paper_to_markdown
from wkhelper.core.results import (
    ExamAnswerResult,
    ExamCoverDetail,
    ExamExportResult,
    ExamListData,
    ExamPaperData,
    ExamStartPreview,
    ExamStartResult,
    ExamSubmitResult,
)

exam_app = App(name="exam", help="考试查询与考场管理")


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
        cover = await plat.get_exam_cover(exam, matched)
        if cover.status == "已缺考":
            next_actions = [
                NextAction(
                    reason="已缺考：截止时间已过且未参加考试，本场不可再进入",
                    argv=[],
                    description=f"考试 {exam} 已逾期，`exam start` 与 `exam submit` 均不会成功",
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
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认进入考场并启动服务端考试计时")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExamStartResult] | CommandResult[ExamStartPreview]:
    """显式确认后向平台发起进入考场请求，启动考场会话与授权令牌。"""
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

        if dry_run:
            preview = ExamStartPreview(
                dry_run=True,
                course_id=str(matched.id),
                exam_id=str(exam),
                title=cover.title,
                limit_minutes=cover.limit_minutes,
                note="预览模式不会向平台申请考试令牌，确认进入考场请传入 `--confirm`",
            )
            return CommandResult[ExamStartPreview](data=preview)

        result = await plat.start_exam(exam, matched)
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
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="只预览将要提交的作答")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认写入服务端作答")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExamAnswerResult]:
    """提交考试单题作答（写入服务端暂存，不改动最终得分）。"""
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
            )
        )

    async with _open_course(course, platform, account) as (plat, matched):
        result = await plat.answer_exam_problem(exam, matched, question, keys)
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
        _ensure_not_overdue(await plat.get_exam_cover(exam, matched))
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
                )
            )

        result = await plat.submit_exam(exam, matched)
        return CommandResult[ExamSubmitResult](data=result)
