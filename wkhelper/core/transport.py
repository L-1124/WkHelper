"""HTTP 传输层统一出口。

全仓库只在该模块构造 HTTP 会话，底层使用 curl-impersonate（`curl_cffi`）伪装
真实浏览器的 TLS/JA3 与 HTTP/2 指纹，降低平台风控命中概率。

同时对外暴露统一的异常别名，避免业务代码直接依赖具体客户端库。
"""

import os
import warnings
from collections.abc import Awaitable, Callable
from typing import Literal

from curl_cffi import AsyncSession
from curl_cffi.requests.exceptions import (
    ConnectionError,
    HTTPError,
    RequestException,
    Timeout,
)

try:  # Windows 的 Proactor 事件循环会触发 curl_cffi 的线程回退告警
    from curl_cffi.aio import CurlCffiWarning
except ImportError:  # pragma: no cover - 旧版本没有该告警类型
    CurlCffiWarning = None

if CurlCffiWarning is not None:
    # stderr 是 NDJSON 事件通道，任何第三方告警都会破坏机器可读契约
    warnings.filterwarnings("ignore", category=CurlCffiWarning)

type Fetcher = Callable[[str], Awaitable[bytes]]
type HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "QUERY"]

# 默认伪装目标；随 curl_cffi 版本升级会自动获得最新浏览器指纹
DEFAULT_IMPERSONATE = "chrome"

# 显式关闭伪装的取值
DISABLED = "none"


# 伪装默认带的是「文档导航」头（Accept: text/html 等）。我们的全部请求都是 API
# 调用，实测雨课堂网关会因 text/html 的 Accept 直接返回 HTTP 500，故统一改成 XHR 语义。
API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def resolve_impersonate(impersonate: str | None = DEFAULT_IMPERSONATE, user_agent: str | None = None) -> str | None:
    """解析最终使用的伪装目标（`None` 表示关闭伪装）。

    优先级：显式 `user_agent` > 环境变量 `WKHELPER_IMPERSONATE` > 平台默认值。
    自定义 UA 与浏览器指纹必然矛盾，因此一旦指定 UA 就关闭伪装。
    """
    if user_agent:
        return None
    target = os.environ.get("WKHELPER_IMPERSONATE", "").strip() or impersonate or DISABLED
    return None if target.lower() == DISABLED else target


def build_session(
    timeout: float = 15.0,
    proxy: str | None = None,
    user_agent: str | None = None,
    impersonate: str | None = DEFAULT_IMPERSONATE,
) -> AsyncSession:
    """构建异步会话。

    指纹伪装会让 UA、Accept 系列请求头与 TLS/HTTP2 指纹来自同一份浏览器配置。
    三者必须一致：实测雨课堂对「UA 与指纹错位」直接返回 HTTP 500。

    平台默认值由 `BasePlatform.impersonate` 声明，因为部分平台的会话 Cookie
    与签发时的 TLS 指纹绑定，换指纹需要重新登录。
    """
    headers: dict[str, str] = dict(API_HEADERS)
    if user_agent:
        headers["User-Agent"] = user_agent
    return AsyncSession(
        impersonate=resolve_impersonate(impersonate, user_agent),
        timeout=timeout,
        proxy=proxy,
        headers=headers,
    )


__all__ = [
    "AsyncSession",
    "ConnectionError",
    "DEFAULT_IMPERSONATE",
    "DISABLED",
    "Fetcher",
    "HTTPError",
    "HttpMethod",
    "RequestException",
    "Timeout",
    "build_session",
    "resolve_impersonate",
]
