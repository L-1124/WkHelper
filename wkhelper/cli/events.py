"""结构化 stderr 事件通道（强类型 JSON 载荷）。"""

import json
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
