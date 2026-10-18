"""WkHelper 命令行主程序入口（基于 Cyclopts）。"""

import logging
import sys

from cyclopts import CycloptsError

from wkhelper.cli.app import app, extract_global_flags
from wkhelper.cli.envelope import create_error_envelope, print_json
from wkhelper.core.exceptions import (
    APIError,
    AuthError,
    ConfirmationRequiredError,
    OutputExistsError,
    PartialFailureError,
    WKError,
)

logger = logging.getLogger("wkhelper")


def main() -> None:
    """命令行同步主入口。"""
    exit_code = 0
    try:
        remaining = extract_global_flags(sys.argv[1:])
        if not remaining:
            app(["--help"])
            sys.exit(0)
        exit_code = app(remaining)
    except CycloptsError as exc:
        print_json(create_error_envelope(code="INVALID_ARGUMENT", message=str(exc), retryable=False))
    except ConfirmationRequiredError as exc:
        print_json(create_error_envelope(code="CONFIRMATION_REQUIRED", message=str(exc), retryable=False, next_actions=exc.next_actions))
        sys.exit(5)
    except OutputExistsError as exc:
        print_json(create_error_envelope(code="FILE_EXISTS", message=str(exc), retryable=False))
        sys.exit(5)
    except PartialFailureError as exc:
        print_json(create_error_envelope(code="PARTIAL_FAILURE", message=str(exc), retryable=False, data=exc.data))
        sys.exit(7)
    except AuthError as exc:
        print_json(create_error_envelope(code="AUTH_EXPIRED", message=str(exc), retryable=False))
        sys.exit(3)
    except (FileNotFoundError, KeyError) as exc:
        print_json(create_error_envelope(code="NOT_FOUND", message=str(exc), retryable=False))
        sys.exit(4)
    except ValueError as exc:
        print_json(create_error_envelope(code="INVALID_ARGUMENT", message=str(exc), retryable=False))
        sys.exit(2)
    except APIError as exc:
        print_json(create_error_envelope(code="PLATFORM_ERROR", message=str(exc), retryable=True))
        sys.exit(6)
    except WKError as exc:
        print_json(create_error_envelope(code="BIZ_ERROR", message=str(exc), retryable=False))
        sys.exit(1)
    except Exception as exc:
        logger.exception("未捕获的内部异常")
        print_json(create_error_envelope(code="INTERNAL_ERROR", message=str(exc), retryable=False))
        sys.exit(1)

    sys.exit(exit_code or 0)


async def async_main(argv: list[str]) -> int:
    """供测试套件异步调用的入口。"""
    try:
        remaining = extract_global_flags(argv)
        if not remaining:
            await app.run_async(["--help"])
            return 0
        res = await app.run_async(remaining)
        return int(res or 0)
    except CycloptsError as exc:
        print_json(create_error_envelope(code="INVALID_ARGUMENT", message=str(exc), retryable=False))
        return 2
    except ConfirmationRequiredError as exc:
        print_json(create_error_envelope(code="CONFIRMATION_REQUIRED", message=str(exc), retryable=False, next_actions=exc.next_actions))
        return 5
    except OutputExistsError as exc:
        print_json(create_error_envelope(code="FILE_EXISTS", message=str(exc), retryable=False))
        return 5
    except PartialFailureError as exc:
        print_json(create_error_envelope(code="PARTIAL_FAILURE", message=str(exc), retryable=False, data=exc.data))
        return 7
    except AuthError as exc:
        print_json(create_error_envelope(code="AUTH_EXPIRED", message=str(exc), retryable=False))
        return 3
    except (FileNotFoundError, KeyError) as exc:
        print_json(create_error_envelope(code="NOT_FOUND", message=str(exc), retryable=False))
        return 4
    except ValueError as exc:
        print_json(create_error_envelope(code="INVALID_ARGUMENT", message=str(exc), retryable=False))
        return 2
    except APIError as exc:
        print_json(create_error_envelope(code="PLATFORM_ERROR", message=str(exc), retryable=True))
        return 6
    except WKError as exc:
        print_json(create_error_envelope(code="BIZ_ERROR", message=str(exc), retryable=False))
        return 1
    except Exception as exc:
        print_json(create_error_envelope(code="INTERNAL_ERROR", message=str(exc), retryable=False))
        return 1


if __name__ == "__main__":
    main()
