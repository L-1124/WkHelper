"""CLI 展现层输出：成功/错误信封与终端渲染，供入口与 result_action 共用。"""

import sys
from collections.abc import Mapping
from typing import Any, cast

from rich.console import Console
from rich.markdown import Markdown
from rich.theme import Theme

from wkhelper.cli.context import get_effective_format
from wkhelper.cli.envelope import CommandResult, create_success_envelope, print_json
from wkhelper.cli.markdown import format_error_markdown, format_result_markdown
from wkhelper.domain.actions import NextAction
from wkhelper.domain.json_types import JsonDict, JsonValue

TERMINAL_THEME = Theme(
    {
        "markdown.code": "bold cyan",
        "markdown.code_block": "cyan",
        "markdown.h1": "bold cyan",
        "markdown.h2": "bold cyan",
        "markdown.h3": "bold",
        "markdown.table.header": "bold cyan",
        "markdown.table.border": "dim",
        "markdown.item.bullet": "bold cyan",
    }
)

CONSOLE = Console(theme=TERMINAL_THEME)


def _print_markdown(content: str, console: Console) -> None:
    """TTY 下走 Rich 渲染，重定向时退化为纯文本写入。"""
    if console.is_terminal:
        console.print(Markdown(content))
    else:
        sys.stdout.write(content + "\n")
        sys.stdout.flush()


def emit_error(envelope: JsonDict, console: Console = CONSOLE) -> None:
    """按生效格式输出错误信封（JSON）或错误 Markdown。"""
    if get_effective_format(console.is_terminal) == "json":
        print_json(envelope)
        return

    raw_error = envelope.get("error")
    error: Mapping[str, JsonValue] = raw_error if isinstance(raw_error, Mapping) else {}
    raw_details = error.get("details")
    raw_actions = envelope.get("next_actions")
    raw_data = envelope.get("data")
    # 信封已由强类型模型生成，此处是 JSON 反查边界，按契约收窄一次即可。
    actions = cast("list[NextAction]", raw_actions) if isinstance(raw_actions, list) else []
    _print_markdown(
        format_error_markdown(
            str(error.get("code", "ERROR")),
            str(error.get("message", "未知错误")),
            dict(raw_details) if isinstance(raw_details, Mapping) else None,
            actions,
            data=raw_data,
        ),
        console,
    )


def unified_result_action(result: Any, console: Console = CONSOLE) -> int:
    """Cyclopts result_action：接管命令返回值，统一输出并返回退出码。"""
    if isinstance(result, int):
        return result
    if isinstance(result, CommandResult):
        if get_effective_format(console.is_terminal) == "json":
            print_json(create_success_envelope(data=result.data, meta=result.meta, next_actions=result.next_actions))
        else:
            _print_markdown(
                format_result_markdown(result.data, meta=result.meta, next_actions=result.next_actions),
                console,
            )
        return result.exit_code
    if result is not None:
        if get_effective_format(console.is_terminal) == "json":
            print_json(create_success_envelope(data=result))
        else:
            _print_markdown(format_result_markdown(result), console)
    return 0


__all__ = [
    "CONSOLE",
    "TERMINAL_THEME",
    "emit_error",
    "unified_result_action",
]
