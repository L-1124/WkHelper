"""请求节流与退避策略：降低触发平台风控的概率。

三个独立机制：
- `RateGate`：进程内统一节奏控制，支持全局限速、抖动与冷却期；
- `RetryPolicy`：瞬时错误（5xx / 429 / 网络）的指数退避重试参数；
- `parse_retry_hint`：从响应头或响应体中提取平台要求的等待秒数。
"""

import asyncio
import random
import re

from pydantic import ConfigDict

from wkhelper.domain.model import Model

# 平台限流提示：响应体形如 "Expected available in 12 second." / "请等待 12 秒"
_RETRY_HINT = re.compile(r"(?:Expected available in|available in|请等待)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:second|秒)?", re.IGNORECASE)


class RetryPolicy(Model):
    """瞬时错误重试策略。"""

    model_config = ConfigDict(frozen=True)

    max_attempts: int = 4
    base_backoff: float = 0.6
    max_backoff: float = 15.0
    jitter_ratio: float = 0.35


class RateGate:
    """进程内自适应节流器。

    `min_interval` 为相邻请求的最小间隔（秒），`jitter_ratio` 在间隔与退避上
    叠加随机抖动，避免固定节奏被识别为脚本流量。冷却期用于平台明确限流后
    全局暂停，防止继续请求扩大处罚。
    """

    def __init__(self, min_interval: float = 0.0, jitter_ratio: float = 0.2) -> None:
        self.min_interval = max(0.0, min_interval)
        self.jitter_ratio = max(0.0, jitter_ratio)
        self._lock = asyncio.Lock()
        self._next_slot = 0.0
        self._cooldown_until = 0.0

    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()

    def _jitter(self, value: float) -> float:
        if value <= 0 or self.jitter_ratio <= 0:
            return max(0.0, value)
        delta = value * self.jitter_ratio
        return max(0.0, value + random.uniform(-delta, delta))

    async def acquire(self) -> None:
        """等待到下一个可用时隙，同时吸收全局冷却期。"""
        if self.min_interval <= 0 and self._cooldown_until <= 0:
            return
        async with self._lock:
            now = self._now()
            wait = max(now, self._next_slot, self._cooldown_until) - now
            interval = self._jitter(self.min_interval)
            self._next_slot = max(now, self._next_slot) + interval
        if wait > 0:
            await asyncio.sleep(wait)

    async def cooldown(self, seconds: float) -> None:
        """设置全局冷却期，所有后续请求都会先等待。"""
        if seconds <= 0:
            return
        async with self._lock:
            self._cooldown_until = max(self._cooldown_until, self._now() + seconds)

    def backoff_delay(self, attempt: int, policy: RetryPolicy) -> float:
        """第 `attempt` 次（从 1 开始）失败后的退避时长。"""
        raw = min(policy.max_backoff, policy.base_backoff * (2 ** max(0, attempt - 1)))
        return self._jitter(raw)


def parse_retry_hint(body: str | None, retry_after: str | None = None) -> float | None:
    """从 `Retry-After` 响应头或响应体中提取平台要求的等待秒数。"""
    if retry_after:
        try:
            return max(0.0, float(retry_after.strip()))
        except ValueError:
            pass
    if not body:
        return None
    match = _RETRY_HINT.search(body)
    if not match:
        return None
    try:
        return max(0.0, float(match.group(1)))
    except ValueError:
        return None
