"""core 层通用工具函数。"""

import random


def get_random_sleep(min_s: float = 3.0, max_s: float = 4.0) -> float:
    """获取模拟人工操作的随机休眠时间（秒）。"""
    return random.uniform(min_s, max_s)
