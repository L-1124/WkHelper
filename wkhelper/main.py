"""WkHelper 命令行主程序入口（基于 Cyclopts）。"""

import asyncio
import inspect
import logging
import sys
import traceback
from collections.abc import Sequence
from typing import Any

from cyclopts import CycloptsError
from pydantic import JsonValue, ValidationError

from wkhelper.cli.app import app
from wkhelper.cli.context import execution_context, get_context
from wkhelper.cli.envelope import create_error_envelope
from wkhelper.cli.output import emit_error
from wkhelper.domain.errors import (
    APIError,
    AuthError,
    ConfirmationRequiredError,
    OutputExistsError,
    PartialFailureError,
    RiskControlError,
    WKError,
)

logger = logging.getLogger("wkhelper")


def _loop_factory() -> asyncio.AbstractEventLoop:
    """选择事件循环实现。

    Windows 默认的 `ProactorEventLoop` 不实现 `add_reader/add_writer`，而
    curl_cffi 依赖它们驱动 libcurl 的 socket action；用 selector 循环可避免
    curl_cffi 退回到「额外选择器线程」的兼容路径。本程序不使用子进程，
    因此不受 selector 循环在 Windows 上的限制影响。
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def _handle_exception(exc: BaseException) -> tuple[dict[str, Any], int]:
    """统一异常到退出码映射，在 --debug 时注入堆栈与类型诊断。"""
    details: dict[str, JsonValue] = {}
    if hasattr(exc, "details") and isinstance(exc.details, dict):
        details.update(exc.details)
    if get_context().options.debug:
        details["exception_type"] = type(exc).__name__
        details["traceback"] = [line for line in traceback.format_exc().strip().splitlines()]

    if isinstance(exc, CycloptsError):
        return create_error_envelope(code="INVALID_ARGUMENT", message=str(exc), retryable=False, details=details), 2
    if isinstance(exc, ConfirmationRequiredError):
        return (
            create_error_envelope(
                code="CONFIRMATION_REQUIRED",
                message=str(exc),
                retryable=False,
                details=details,
                next_actions=exc.next_actions,
            ),
            5,
        )
    if isinstance(exc, OutputExistsError):
        return create_error_envelope(code="FILE_EXISTS", message=str(exc), retryable=False, details=details), 5
    if isinstance(exc, PartialFailureError):
        return (
            create_error_envelope(
                code="PARTIAL_FAILURE",
                message=str(exc),
                retryable=False,
                details=details,
                data=exc.data,
                next_actions=exc.next_actions,
            ),
            7,
        )
    if isinstance(exc, AuthError):
        return create_error_envelope(code="AUTH_EXPIRED", message=str(exc), retryable=False, details=details), 3
    if isinstance(exc, RiskControlError):
        details_map = dict(details)
        if exc.web_url:
            details_map["web_url"] = exc.web_url
            details_map["guidance"] = "请在浏览器打开 web_url 页面完成滑块人机验证，通过后重新运行当前命令即可。"
        return (
            create_error_envelope(
                code="RISK_CONTROL_REQUIRED",
                message=str(exc),
                retryable=False,
                details=details_map,
                next_actions=exc.next_actions,
            ),
            6,
        )
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return create_error_envelope(code="NOT_FOUND", message=str(exc), retryable=False, details=details), 4
    if isinstance(exc, ValidationError):
        logger.exception("内部数据契约校验失败")
        return create_error_envelope(code="INTERNAL_ERROR", message="内部数据契约校验失败", details=details), 1
    if isinstance(exc, ValueError):
        return create_error_envelope(code="INVALID_ARGUMENT", message=str(exc), retryable=False, details=details), 2
    if isinstance(exc, APIError):
        return (
            create_error_envelope(
                code="PLATFORM_ERROR",
                message=str(exc),
                retryable=exc.retryable,
                details=details,
                data=exc.data,
                next_actions=exc.next_actions,
            ),
            6,
        )
    if isinstance(exc, WKError):
        return create_error_envelope(code="BIZ_ERROR", message=str(exc), retryable=False, details=details), 1

    logger.exception("未捕获的内部异常")
    return create_error_envelope(code="INTERNAL_ERROR", message=str(exc), retryable=False, details=details), 1


async def async_main(argv: Sequence[str]) -> int:
    """异步入口：供测试与嵌入调用，不产生 SystemExit。"""
    with execution_context():
        try:
            command, bound, _ = app.meta.parse_args(list(argv))
            result = command(*bound.args, **bound.kwargs)
            if inspect.isawaitable(result):
                result = await result
            return int(result or 0)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, SystemExit):
                return exc.code if isinstance(exc.code, int) else 0
            envelope, code = _handle_exception(exc)
            emit_error(envelope)
            return code


def main() -> None:
    """同步入口：在受控事件循环中运行异步命令。

    Cyclopts 负责同步解析，业务协程由本入口的 selector 循环执行，
    避免 curl_cffi 在 Windows 上退回额外选择器线程。
    """
    try:
        code = asyncio.run(async_main(sys.argv[1:]), loop_factory=_loop_factory)
    except BaseException as exc:
        if isinstance(exc, SystemExit):
            raise
        with execution_context():
            envelope, exit_code = _handle_exception(exc)
            emit_error(envelope)
        sys.exit(exit_code)
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
