"""HTTP 传输层统一出口。

全仓库只在该模块构造 HTTP 会话，底层使用 curl-impersonate（`curl_cffi`）伪装
真实浏览器的 TLS/JA3 与 HTTP/2 指纹，降低平台风控命中概率。

同时对外暴露统一的异常别名，避免业务代码直接依赖具体客户端库。
"""

import asyncio
import logging
import os
import time
import warnings
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from urllib.parse import urlsplit

from curl_cffi import AsyncSession
from curl_cffi.requests.cookies import CookieConflict
from curl_cffi.requests.exceptions import (
    ConnectionError,
    HTTPError,
    RequestException,
    Timeout,
)

from wkhelper.domain.errors import APIError, AuthError, RiskControlError
from wkhelper.domain.json_types import JsonDict
from wkhelper.infra.clock import ServerClock
from wkhelper.infra.events import emit_event, is_debug_enabled
from wkhelper.infra.throttle import RateGate, RetryPolicy, parse_retry_hint

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


logger = logging.getLogger(__name__)
_RISK_CONTROL_CODE = "400403"
HttpConnectionError = ConnectionError
HttpTimeout = Timeout
HttpError = HTTPError


def _looks_like_risk_control(body: str | dict[str, Any] | None) -> bool:
    """识别风控人机验证响应（`error_code: 400403`）。"""
    if isinstance(body, dict):
        return str(body.get("error_code") or "") == _RISK_CONTROL_CODE
    if isinstance(body, str):
        return _RISK_CONTROL_CODE in body
    return False


def trace_http(method: str, url: str, start: float, status_code: int | None = None, error: str | None = None) -> None:
    """仅在调试模式下发射 HTTP 追踪事件，避免污染标准输出。"""
    if not is_debug_enabled():
        return
    payload: JsonDict = {
        "method": method.upper(),
        "url": url,
        "status_code": status_code,
        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
    }
    if error is not None:
        payload["error"] = error
    emit_event("http.trace", payload)


async def request_json(
    client: AsyncSession, gate: RateGate, retry_policy: RetryPolicy, clock: ServerClock, method: HttpMethod, url: str, **kwargs: Any
) -> dict[str, Any]:
    """发起 HTTP 请求并解析 JSON；瞬时错误按退避与冷却策略重试。"""
    policy = retry_policy
    for attempt in range(1, policy.max_attempts + 1):
        await gate.acquire()
        try:
            return await request_json_once(client, clock, method, url, **kwargs)
        except APIError as exc:
            if not exc.retryable or attempt >= policy.max_attempts:
                raise
            delay = exc.retry_after if exc.retry_after is not None else gate.backoff_delay(attempt, policy)
            logger.debug(f"{method} {url} 第 {attempt} 次失败，{delay:.2f}s 后重试: {exc}")
            if exc.retry_after is not None:
                await gate.cooldown(delay)
            else:
                await asyncio.sleep(delay)
    raise APIError(f"请求失败次数过多: {url}")  # 循环必然在内部返回或抛出


async def request_json_once(client: AsyncSession, clock: ServerClock, method: HttpMethod, url: str, **kwargs: Any) -> dict[str, Any]:
    """单次请求：HTTP 失败与限流提示统一收敛为带重试元数据的错误。"""
    cookies = client.cookies
    csrftoken = None
    if cookies is not None:
        try:
            csrftoken = cookies.get("csrftoken")
        except CookieConflict:
            # 跨域会话（如考场子系统）可能同时存在多个 csrftoken，按当前 host 取
            csrftoken = cookies.get("csrftoken", domain=urlsplit(url).hostname or "")
    headers = client.headers
    if csrftoken and headers is not None and headers.get("X-CSRFToken") != csrftoken:
        headers["X-CSRFToken"] = csrftoken
    start = time.perf_counter()
    try:
        resp = await client.request(method, url, **kwargs)
    except (HttpConnectionError, HttpTimeout) as exc:
        trace_http(method, url, start, None, str(exc))
        raise APIError(f"网络请求失败: {url}") from exc

    body = resp.text or ""
    try:
        resp.raise_for_status()
    except HttpError as exc:
        trace_http(method, url, start, resp.status_code, str(exc))
        if _looks_like_risk_control(body):
            raise RiskControlError(f"触发平台风控人机验证，请先在浏览器完成验证后重试: {url}") from exc
        if resp.status_code in (401, 403):
            raise AuthError(f"平台拒绝访问（HTTP {resp.status_code}），登录态可能已失效: {url}") from exc
        raise APIError(
            f"平台请求失败（HTTP {resp.status_code}）: {url}",
            status_code=resp.status_code,
            retry_after=parse_retry_hint(body, resp.headers.get("Retry-After")),
            retryable=resp.status_code == 429 or resp.status_code >= 500,
        ) from exc

    hint = parse_retry_hint(body, resp.headers.get("Retry-After"))
    if hint is not None:
        trace_http(method, url, start, resp.status_code, "rate limited")
        raise APIError(f"平台限流，需等待 {hint:.0f}s: {url}", status_code=resp.status_code, retry_after=hint)

    try:
        payload = resp.json()
    except ValueError as exc:
        trace_http(method, url, start, resp.status_code, str(exc))
        raise APIError(f"平台响应不是合法 JSON: {url}") from exc
    trace_http(method, url, start, resp.status_code)
    if not isinstance(payload, dict):
        raise APIError(f"平台响应不是 JSON 对象: {url}")
    if _looks_like_risk_control(payload):
        raise RiskControlError(f"触发平台风控人机验证，请先在浏览器完成验证后重试: {url}")
    clock.sync(resp.headers, payload)
    return payload
