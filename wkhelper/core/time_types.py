"""时间单位契约：对外统一使用 epoch 毫秒。

平台原始载荷的时间戳单位并不一致（秒/毫秒/字符串），解析边界统一经
`epoch_millis()` 归一到 `EpochMillis`，展示层不再猜测单位。
"""

import time
from typing import Any, NewType

EpochMillis = NewType("EpochMillis", int)

_SECONDS_UPPER_BOUND = 100_000_000_000


def epoch_millis(value: Any) -> EpochMillis | None:
    """把平台时间戳归一为毫秒；秒级输入自动升级，无效值返回 None。"""
    if value is None or value == "":
        return None
    try:
        ts = int(float(value))
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return EpochMillis(ts if ts >= _SECONDS_UPPER_BOUND else ts * 1000)


def now_millis() -> EpochMillis:
    """当前时间（epoch 毫秒）。"""
    return EpochMillis(int(time.time() * 1000))
