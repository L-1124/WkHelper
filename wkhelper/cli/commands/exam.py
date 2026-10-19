"""考试查询、考场封面检索与进入考场命令。"""

from typing import Annotated

from cyclopts import App, Parameter

from wkhelper.cli.context import PlatformCode
from wkhelper.cli.deps import _open_course, _paginate
from wkhelper.cli.envelope import CommandResult, NextAction
from wkhelper.core.exceptions import ConfirmationRequiredError
from wkhelper.core.results import ExamCoverDetail, ExamListData, ExamStartPreview, ExamStartResult

exam_app = App(name="exam", help="考试查询与考场管理")


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
        return CommandResult[ExamCoverDetail](data=cover)


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
