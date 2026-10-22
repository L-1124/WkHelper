"""平台心跳协议数据。"""

import random

from pydantic import ConfigDict

from wkhelper.domain.model import Model


def create_page_nonce() -> str:
    """页面编号后缀，算法对齐前端 `Math.floor(1048576*(1+random)).toString(36)`。"""
    return _base36(int(1048576 * (1 + random.random())))


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value <= 0:
        return "0"
    out: list[str] = []
    while value:
        value, rem = divmod(value, 36)
        out.append(digits[rem])
    return "".join(reversed(out))


class HeartbeatProfile(Model):
    """心跳报文中随平台变化的固定字段，由平台适配层显式提供。

    `lob` 是业务线标识（雨课堂 `ykt`、学堂在线 `xt`）；`n` 是 CDN 代号，
    只有解析出播放源时才有意义，未知时留空；`sp` 为播放倍速。
    """

    model_config = ConfigDict(frozen=True)

    lob: str
    n: str = ""
    sp: float = 1.0
    interval: int = 5
