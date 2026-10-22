"""服务器时钟校准。"""

import logging
import time
from typing import Any

from wkhelper.domain.time_types import epoch_millis, parse_http_date_ms

logger = logging.getLogger(__name__)


def _header_value(headers: Any, name: str) -> str | None:
    """按不区分大小写的方式读取响应头（兼容普通 dict 与 Headers 对象）。"""
    if not headers:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    return getter(name) or getter(name.title()) or getter(name.upper())


class ServerClock:
    _MAX_CLOCK_SKEW_MS = 6 * 3600 * 1000
    _CLOCK_SMOOTHING = 0.3

    def __init__(self) -> None:
        self.offset_ms: float = 0.0
        self._clock_samples = 0

    def now_ms(self) -> int:
        return int(time.time() * 1000 + self.offset_ms)

    def sync(self, headers: Any, payload: dict[str, Any]) -> None:
        """用响应中的 server_time 或 Date 头校正本机时钟，供心跳与作答时间戳使用。"""
        server_ms = self._payload_server_time_ms(payload)
        if server_ms is None:
            server_ms = parse_http_date_ms(_header_value(headers, "date"))
        if server_ms is None:
            return
        sample = server_ms - time.time() * 1000
        if abs(sample) > self._MAX_CLOCK_SKEW_MS:
            logger.warning(f"忽略异常的服务器时钟样本: {sample:.0f}ms")
            return
        if self._clock_samples == 0:
            self.offset_ms = sample
        else:
            alpha = self._CLOCK_SMOOTHING
            self.offset_ms = (1 - alpha) * self.offset_ms + alpha * sample
        self._clock_samples += 1

    @staticmethod
    def _payload_server_time_ms(payload: dict[str, Any]) -> int | None:
        """优先取响应体里的 server_time，支持顶层或 data 内嵌、秒级自动升级。"""
        value = payload.get("server_time")
        if value is None:
            data = payload.get("data")
            value = data.get("server_time") if isinstance(data, dict) else None
        ms = epoch_millis(value)
        return int(ms) if ms is not None else None
