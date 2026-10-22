"""结构化 stderr 事件通道（强类型 JSON 载荷）。"""

import json
import logging
import sys
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import JsonValue

type EventCallback = Callable[[str, dict[str, JsonValue]], None]


def notifier(callback: EventCallback | None) -> EventCallback:
    """统一事件派发口径：优先外部回调，缺省写入 NDJSON 通道。"""

    def _notify(event: str, payload: dict[str, JsonValue]) -> None:
        if callback is not None:
            callback(event, payload)
        else:
            emit_event(event, payload)

    return _notify


def emit_event(event_type: str, payload: Mapping[str, JsonValue]) -> None:
    """向标准错误输出发送单行 NDJSON 事件。"""
    event: dict[str, JsonValue] = {
        "event": event_type,
        "timestamp": time.time(),
        "payload": dict(payload),
    }
    sys.stderr.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stderr.flush()


class NDJSONLogHandler(logging.Handler):
    """将 Python 标准 logging 输出桥接为结构化 NDJSON 事件。"""

    def emit(self, record: logging.LogRecord) -> None:
        if not is_debug_enabled():
            return
        try:
            msg = self.format(record)
            payload: dict[str, JsonValue] = {
                "name": record.name,
                "level": record.levelname,
                "message": msg,
            }
            emit_event("debug.log", payload)
        except Exception:
            self.handleError(record)


_DEBUG_ENABLED: ContextVar[bool] = ContextVar("debug_enabled", default=False)


def set_debug_enabled(enabled: bool) -> None:
    """只修改当前调用的调试状态，日志桥接器按需安装一次。"""
    _DEBUG_ENABLED.set(enabled)
    if enabled:
        configure_debug_logging()


def is_debug_enabled() -> bool:
    """查询当前调用的调试开关。"""
    return _DEBUG_ENABLED.get()


@contextmanager
def debug_scope(enabled: bool) -> Generator[None]:
    """退出后恢复外层调试状态，不拆装其他调用的日志处理器。"""
    token = _DEBUG_ENABLED.set(enabled)
    try:
        if enabled:
            configure_debug_logging()
        yield
    finally:
        _DEBUG_ENABLED.reset(token)


def configure_debug_logging() -> None:
    """安装共享桥接器；是否发送日志由当前调用状态决定。"""
    logger = logging.getLogger("wkhelper")
    if not any(isinstance(handler, NDJSONLogHandler) for handler in logger.handlers):
        handler = NDJSONLogHandler()
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
