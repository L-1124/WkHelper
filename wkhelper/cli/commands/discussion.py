"""讨论题查询与互动回复命令。"""

from typing import Annotated, Literal

from cyclopts import App, Parameter

from wkhelper.cli.context import PlatformCode
from wkhelper.cli.deps import _open_course, _paginate
from wkhelper.cli.envelope import CommandResult
from wkhelper.discussion.service import get_discussion_detail, get_discussions, submit_discussion
from wkhelper.domain.content import DiscussionCommentResult, DiscussionListData, DiscussionTopicDetail
from wkhelper.domain.errors import APIError, ConfirmationRequiredError

discussion_app = App(name="discussion", help="讨论题查询与互动回复")


@discussion_app.command(name="list")
async def discussion_list_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    status: Annotated[Literal["pending", "completed", "all"], Parameter(name=["--status"], help="状态过滤")] = "all",
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
    limit: Annotated[int, Parameter(name=["--limit"], help="每页条数")] = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
) -> CommandResult[DiscussionListData]:
    """查询课程讨论题列表。"""
    async with _open_course(course, platform, account) as (plat, matched):
        discussions = await get_discussions(plat, matched)
        items = [
            d
            for d in discussions
            if status == "all" or (status == "completed" and d.completed) or (status == "pending" and not d.completed)
        ]
        page_items, meta = _paginate(items, limit, cursor)
        return CommandResult(data=DiscussionListData(discussions=page_items), meta=meta.to_dict())


@discussion_app.command(name="inspect")
async def discussion_inspect_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    discussion: Annotated[str, Parameter(name=["--discussion", "-d"], help="讨论题节点 ID")],
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[DiscussionTopicDetail]:
    """查看讨论题详情与教师要求。"""
    async with _open_course(course, platform, account) as (plat, matched):
        detail = await get_discussion_detail(plat, discussion, matched)
        return CommandResult(data=detail)


@discussion_app.command(name="reply")
async def discussion_reply_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    discussion: Annotated[str, Parameter(name=["--discussion", "-d"], help="讨论题节点 ID")],
    content: Annotated[str, Parameter(name=["--content"], help="回复/发言内容")],
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="仅校验，不提交")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认提交回复")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[DiscussionCommentResult]:
    """发表讨论题回复完成平时分打卡。"""
    if not content.strip():
        raise ValueError("回复内容不能为空")
    if dry_run:
        return CommandResult(
            data=DiscussionCommentResult(
                course_id=str(course),
                leaf_id=str(discussion),
                topic_id=0,
                comment_id=None,
                success=True,
                message="[dry-run] 校验成功",
            )
        )
    if not confirm:
        raise ConfirmationRequiredError("发表讨论回复必须显式附加 `--confirm` 参数")
    async with _open_course(course, platform, account) as (plat, matched):
        result = await submit_discussion(plat, discussion, matched, content.strip())
        if not result.success:
            raise APIError(f"讨论题回复失败: {result.message}")
        return CommandResult(data=result)


discussion_list = discussion_list_cmd
discussion_inspect = discussion_inspect_cmd
discussion_reply = discussion_reply_cmd

__all__ = [
    "discussion_app",
    "discussion_inspect",
    "discussion_inspect_cmd",
    "discussion_list",
    "discussion_list_cmd",
    "discussion_reply",
    "discussion_reply_cmd",
]
