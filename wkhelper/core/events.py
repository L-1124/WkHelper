"""结构化 stderr 事件通道（强类型 JSON 载荷）。"""

import json
import logging
import sys
import time
from collections.abc import Mapping

from wkhelper.core.models import JsonValue


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


# 兼容别名
NDJsonHandler = NDJSONLogHandler

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
