"""课程公告与通知命令。"""

from typing import Annotated, Literal

from cyclopts import App, Parameter

from wkhelper.cli.context import PlatformCode
from wkhelper.cli.deps import _open_course, _paginate
from wkhelper.cli.envelope import CommandResult
from wkhelper.cli.inputs import Limit, validate_positive_limit
from wkhelper.domain.content import NoticeDetail, NoticeListData
from wkhelper.notice.service import get_notices

notice_app = App(name="notice", help="课程公告与通知")


@notice_app.command(name="list")
async def notice_list_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    status: Annotated[Literal["pending", "completed", "all"], Parameter(name=["--status"], help="状态过滤（未读/已读）")] = "all",
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account"], help="账号 ID")] = None,
    limit: Limit = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
) -> CommandResult[NoticeListData]:
    """查询课程公告/通知节点列表。"""
    validate_positive_limit(int, limit)
    async with _open_course(course, platform, account) as (plat, matched):
        notices = await get_notices(plat, matched)
        items = [
            n for n in notices if status == "all" or (status == "completed" and n.completed) or (status == "pending" and not n.completed)
        ]
        page_items, meta = _paginate(items, limit, cursor)
        return CommandResult(data=NoticeListData(notices=page_items), meta=meta.model_dump())


@notice_app.command(name="inspect")
async def notice_inspect_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    notice: Annotated[str, Parameter(name=["--notice", "-n"], help="公告节点 ID")],
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[NoticeDetail]:
    """查看公告标题、正文与发布信息。"""
    async with _open_course(course, platform, account) as (plat, matched):
        detail = await plat.get_notice_detail(notice, matched)
        return CommandResult(data=detail)


notice_list = notice_list_cmd
notice_inspect = notice_inspect_cmd

__all__ = [
    "notice_app",
    "notice_inspect",
    "notice_inspect_cmd",
    "notice_list",
    "notice_list_cmd",
]
