"""CLI 系统状态与契约自省命令。"""

from typing import Annotated

from cyclopts import Parameter
from pydantic import JsonValue

from wkhelper.cli.context import get_context
from wkhelper.cli.envelope import CommandResult
from wkhelper.cli.spec import get_command_schema
from wkhelper.domain.accounts import StatusData
from wkhelper.domain.actions import NextAction


async def status_cmd() -> CommandResult[StatusData]:
    """查看本地环境与账号概况。"""
    store = get_context().get_store()
    accounts = store.list_accounts()
    data = StatusData(
        credential_storage=store.get_storage_status(),
        accounts_count=len(accounts),
        accounts=accounts,
    )
    next_actions: list[NextAction] = []
    if not accounts and not get_context().options.cookie_file:
        next_actions.append(NextAction(reason="登录账号", argv=["wkhelper", "auth", "login", "--platform", "ykt", "--method", "qrcode"]))
    else:
        next_actions.append(NextAction(reason="查看课程列表", argv=["wkhelper", "course", "list"]))
    return CommandResult(data=data, next_actions=next_actions)


async def schema_cmd(
    path: Annotated[str | None, Parameter(help="命令路径，例如 'video learn'")] = None,
    full: Annotated[bool, Parameter(name=["--full"], help="展开完整参数详情")] = False,
) -> CommandResult[dict[str, JsonValue]]:
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
