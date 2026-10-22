"""基于 Cyclopts 的现代类型驱动 CLI 应用装配。

架构约定：

- 业务命令只返回 `CommandResult`，不直接打印；输出与退出码由统一处理器接管。
- 全局会话参数由 meta 应用的 Pydantic 参数模型声明，Cyclopts 负责解析与帮助展示。
- 渲染相关的 `console` 与 `help_formatter` 只在根应用声明，子应用自动继承。
"""

import inspect
from collections.abc import Sequence
from typing import Annotated

from cyclopts import App, Group, Parameter

from wkhelper.cli.commands.article import article_app
from wkhelper.cli.commands.auth import auth_app
from wkhelper.cli.commands.course import course_app
from wkhelper.cli.commands.discussion import discussion_app
from wkhelper.cli.commands.exam import exam_app
from wkhelper.cli.commands.homework import homework_app
from wkhelper.cli.commands.notice import notice_app
from wkhelper.cli.commands.question import question_app, questions_app
from wkhelper.cli.commands.system import schema_cmd, status_cmd
from wkhelper.cli.commands.video import video_app
from wkhelper.cli.context import SessionOptions, get_context
from wkhelper.cli.output import CONSOLE, unified_result_action
from wkhelper.infra.events import set_debug_enabled

app = App(
    name="wkhelper",
    help="WkHelper: 面向 AI Agent 的网课自动化 CLI",
    help_formatter="plain",
    console=CONSOLE,
    result_action=unified_result_action,
    exit_on_error=False,
    print_error=False,
)

# 挂载子应用
app.command(auth_app)
app.command(course_app)
app.command(video_app)
app.command(homework_app)
app.command(question_app)
app.command(questions_app)
app.command(article_app)
app.command(discussion_app)
app.command(notice_app)
app.command(exam_app)

# 挂载根命令
app.command(status_cmd, name="status")
app.command(schema_cmd, name="schema")

# meta 应用承载全局会话参数：独立成组并排在帮助页最前，输出由根应用统一渲染。
app.meta.help = "全局会话参数（可置于子命令前后）"
app.meta.help_formatter = "plain"
app.meta.console = CONSOLE
app.meta.group_parameters = Group("Session Parameters", sort_key=0)
app.meta.exit_on_error = False
app.meta.print_error = False
app.meta.result_action = "return_value"


@app.meta.default
async def launcher(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    options: Annotated[SessionOptions | None, Parameter(name="*")] = None,
) -> int:
    """解析全局会话参数后转交业务命令。"""
    options = options if options is not None else SessionOptions()
    get_context().options = options
    set_debug_enabled(options.debug)
    return await dispatch(tokens)


async def dispatch(tokens: Sequence[str]) -> int:
    """解析后执行命令，避免 Cyclopts 的共享调用栈跨越 await。"""
    command, bound, _ = app.parse_args(list(tokens) if tokens else ["--help"])
    result = command(*bound.args, **bound.kwargs)
    if inspect.isawaitable(result):
        result = await result
    return unified_result_action(result)


__all__ = [
    "app",
    "dispatch",
    "launcher",
]
