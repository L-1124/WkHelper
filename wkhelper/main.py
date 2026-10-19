"""WkHelper 命令行主程序入口（基于 Cyclopts）。"""

import logging
import sys
import traceback
from collections.abc import Sequence
from typing import Any

from cyclopts import CycloptsError

from wkhelper.cli.app import app, ctx
from wkhelper.cli.envelope import create_error_envelope
from wkhelper.cli.output import emit_error
from wkhelper.core.exceptions import (
    APIError,
    AuthError,
    ConfirmationRequiredError,
    OutputExistsError,
    PartialFailureError,
    WKError,
)
from wkhelper.core.json_types import JsonDict

logger = logging.getLogger("wkhelper")


def _handle_exception(exc: BaseException) -> tuple[dict[str, Any], int]:
    """统一异常到退出码映射，在 --debug 时注入堆栈与类型诊断。"""
    details: JsonDict = {}
    if hasattr(exc, "details") and isinstance(exc.details, dict):
        details.update(exc.details)
    if ctx.debug:
        details["exception_type"] = type(exc).__name__
        details["traceback"] = traceback.format_exc().strip().splitlines()

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
            create_error_envelope(code="PARTIAL_FAILURE", message=str(exc), retryable=False, details=details, data=exc.data),
            7,
        )
    if isinstance(exc, AuthError):
        return create_error_envelope(code="AUTH_EXPIRED", message=str(exc), retryable=False, details=details), 3
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return create_error_envelope(code="NOT_FOUND", message=str(exc), retryable=False, details=details), 4
    if isinstance(exc, ValueError):
        return create_error_envelope(code="INVALID_ARGUMENT", message=str(exc), retryable=False, details=details), 2
    if isinstance(exc, APIError):
        return create_error_envelope(code="PLATFORM_ERROR", message=str(exc), retryable=True, details=details), 6
    if isinstance(exc, WKError):
        return create_error_envelope(code="BIZ_ERROR", message=str(exc), retryable=False, details=details), 1

    logger.exception("未捕获的内部异常")
    return create_error_envelope(code="INTERNAL_ERROR", message=str(exc), retryable=False, details=details), 1


async def async_main(argv: Sequence[str]) -> int:
    """异步入口：供测试与嵌入调用，不产生 SystemExit。"""
    ctx.configure()
    try:
        return int(await app.meta.run_async(list(argv)) or 0)
    except BaseException as exc:
        if isinstance(exc, SystemExit):
            return exc.code if isinstance(exc.code, int) else 0
        envelope, code = _handle_exception(exc)
        emit_error(envelope)
        return code


def main() -> None:
    """同步入口：Cyclopts 内部以 asyncio 驱动异步命令。"""
    ctx.configure()
    try:
        code = int(app.meta(sys.argv[1:]) or 0)
    except BaseException as exc:
        if isinstance(exc, SystemExit):
            raise
        envelope, exit_code = _handle_exception(exc)
        emit_error(envelope)
        sys.exit(exit_code)
    sys.exit(code)


if __name__ == "__main__":
    main()
