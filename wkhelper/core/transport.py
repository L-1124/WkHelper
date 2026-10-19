"""资源传输抽象。

`core` 只依赖「按 URL 取字节」这一能力，不直接持有具体 HTTP 客户端；
实现由平台适配层提供（`BasePlatform.fetch_bytes`），测试也可注入轻量假实现。
"""

from collections.abc import Awaitable, Callable

type Fetcher = Callable[[str], Awaitable[bytes]]

__all__ = ["Fetcher"]
