"""基于 Cyclopts 的现代类型驱动 CLI 应用装配。

架构约定：

- 业务命令只返回 `CommandResult`，不直接打印；输出与退出码由 `result_action` 统一接管。
- 全局会话参数（`--format`/`--debug`/`--proxy` 等）声明在 meta 应用上，由 Cyclopts 负责
  解析、校验与帮助展示，不再手工扫描 `sys.argv`。
- 渲染相关的 `console` 与 `help_formatter` 只在根应用声明，子应用自动继承。
"""

from collections.abc import Sequence
from typing import Annotated

from cyclopts import App, Group, Parameter

from wkhelper.cli.commands.article import article_app
from wkhelper.cli.commands.auth import auth_app
from wkhelper.cli.commands.course import course_app
from wkhelper.cli.commands.discussion import discussion_app
from wkhelper.cli.commands.exam import exam_app
from wkhelper.cli.commands.homework import homework_app
from wkhelper.cli.commands.question import question_app, questions_app
from wkhelper.cli.commands.system import schema_cmd, status_cmd
from wkhelper.cli.commands.video import video_app
from wkhelper.cli.context import CredentialMode, OutputFormat, ctx
from wkhelper.cli.output import CONSOLE, unified_result_action

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
    format: Annotated[OutputFormat | None, Parameter(name=["--format"], help="输出格式：json 或 md（缺省按 TTY 推断）")] = None,
    data_dir: Annotated[str | None, Parameter(name=["--data-dir"], help="凭据与检查点数据目录")] = None,
    credential_mode: Annotated[CredentialMode, Parameter(name=["--credential-mode"], help="凭据存储模式")] = "system",
    cookie_file: Annotated[
        str | None, Parameter(name=["--cookie-file"], help="会话 Cookie 文件（需配合 `--credential-mode session`）")
    ] = None,
    debug: Annotated[bool, Parameter(name=["--debug"], help="输出调试堆栈与 NDJSON 事件")] = False,
    user_agent: Annotated[str | None, Parameter(name=["--user-agent"], help="覆盖请求 User-Agent")] = None,
    proxy: Annotated[str | None, Parameter(name=["--proxy"], help="HTTP 代理地址")] = None,
) -> int:
    """解析全局会话参数后转交业务命令。"""
    ctx.configure(
        format=format,
        data_dir=data_dir,
        credential_mode=credential_mode,
        cookie_file=cookie_file,
        debug=debug,
        user_agent=user_agent,
        proxy=proxy,
    )
    return await dispatch(tokens)


async def dispatch(tokens: Sequence[str]) -> int:
    """执行剥离全局参数后的业务命令；空命令时输出帮助。

    Cyclopts 在嵌套调用（meta 启动器内）会把 `result_action` 强制降级为
    `return_value`，因此这里显式调用统一的输出处理器完成渲染与退出码换算。
    """
    if not tokens:
        await app.run_async(["--help"])
        return 0
    return int(unified_result_action(await app.run_async(list(tokens))) or 0)


__all__ = [
    "app",
    "dispatch",
    "launcher",
]
