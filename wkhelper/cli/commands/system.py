"""CLI 系统状态与契约自省命令。"""

from typing import Annotated

from cyclopts import Parameter

from wkhelper.cli.context import ctx
from wkhelper.cli.envelope import CommandResult, NextAction
from wkhelper.cli.spec import get_command_schema
from wkhelper.core.json_types import JsonDict
from wkhelper.core.results import StatusData


async def status_cmd() -> CommandResult[StatusData]:
    """查看本地环境与账号概况。"""
    store = ctx.get_store()
    accounts = store.list_accounts()
    data = StatusData(
        credential_storage=store.get_storage_status(),
        accounts_count=len(accounts),
        accounts=accounts,
    )
    next_actions: list[NextAction] = []
    if not accounts and not ctx.cookie_file:
        next_actions.append(NextAction(reason="登录账号", argv=["wkhelper", "auth", "login", "--platform", "ykt", "--method", "qrcode"]))
    else:
        next_actions.append(NextAction(reason="查看课程列表", argv=["wkhelper", "course", "list"]))
    return CommandResult(data=data, next_actions=next_actions)


async def schema_cmd(
    path: Annotated[str | None, Parameter(help="命令路径，例如 'video learn'")] = None,
    full: Annotated[bool, Parameter(name=["--full"], help="展开完整参数详情")] = False,
) -> CommandResult[JsonDict]:
    """CLI 契约自省。"""
    return CommandResult(data=get_command_schema(path, full=full))


status = status_cmd
schema = schema_cmd

__all__ = [
    "schema",
    "schema_cmd",
    "status",
    "status_cmd",
]
