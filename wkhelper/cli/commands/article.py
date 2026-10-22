"""资料与图文任务管理命令。"""

from typing import Annotated, Literal

from cyclopts import App, Parameter

from wkhelper.article.service import get_articles, read_article
from wkhelper.cli.context import PlatformCode
from wkhelper.cli.deps import _open_course, _paginate
from wkhelper.cli.envelope import CommandResult
from wkhelper.cli.inputs import Limit, validate_positive_limit
from wkhelper.domain.content import ArticleListData, ArticleReadResult
from wkhelper.domain.errors import APIError, ConfirmationRequiredError

article_app = App(name="article", help="资料与图文任务管理")


@article_app.command(name="list")
async def article_list_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    status: Annotated[Literal["pending", "completed", "all"], Parameter(name=["--status"], help="状态过滤")] = "all",
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
    limit: Limit = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
) -> CommandResult[ArticleListData]:
    """查询课程资料/图文节点列表。"""
    validate_positive_limit(int, limit)
    async with _open_course(course, platform, account) as (plat, matched):
        articles = await get_articles(plat, matched)
        items = [
            a for a in articles if status == "all" or (status == "completed" and a.completed) or (status == "pending" and not a.completed)
        ]
        page_items, meta = _paginate(items, limit, cursor)
        return CommandResult(data=ArticleListData(articles=page_items), meta=meta.model_dump())


@article_app.command(name="read")
async def article_read_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    article: Annotated[str, Parameter(name=["--article", "-a"], help="资料/图文节点 ID")],
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="仅校验，不提交")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认执行标记已读")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account"], help="账号 ID")] = None,
) -> CommandResult[ArticleReadResult]:
    """标记指定资料/图文节点为已读已完成。"""
    if dry_run:
        return CommandResult(
            data=ArticleReadResult(
                course_id=str(course),
                leaf_id=str(article),
                name=f"资料 {article}",
                success=True,
                completed=True,
            )
        )
    if not confirm:
        raise ConfirmationRequiredError("标记资料已读必须显式附加 `--confirm` 参数")
    async with _open_course(course, platform, account) as (plat, matched):
        result = await read_article(plat, article, matched)
        if not result.success or not result.completed:
            raise APIError(f"资料 {article} 标记完成失败，平台未确认成功", retryable=False)
        return CommandResult(data=result)


article_list = article_list_cmd
article_read = article_read_cmd

__all__ = [
    "article_app",
    "article_list",
    "article_list_cmd",
    "article_read",
    "article_read_cmd",
]
