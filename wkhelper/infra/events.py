"""结构化 stderr 事件通道（强类型 JSON 载荷）。"""

import json
import logging
import sys
import time
from collections.abc import Callable, Mapping

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


_DEBUG_ENABLED: bool = False


def set_debug_enabled(enabled: bool) -> None:
    """设置全局调试状态并同步配置日志。"""
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = enabled
    configure_debug_logging(enabled)


def is_debug_enabled() -> bool:
    """查询全局调试是否开启。"""
    return _DEBUG_ENABLED


def configure_debug_logging(enabled: bool) -> None:
    """按调试开关配置 wkhelper 根记录器。"""
    logger = logging.getLogger("wkhelper")
    logger.handlers = [h for h in logger.handlers if not isinstance(h, NDJSONLogHandler)]
    if enabled:
        handler = NDJSONLogHandler()
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
