"""wkhelper 核心异常定义（强类型 NextAction 约束）。"""

from wkhelper.domain.actions import NextAction


class WKError(Exception):
    """wkhelper 基础异常。"""


class AuthError(WKError):
    """身份验证失败。"""


class APIError(WKError):
    """API 请求失败。

    `status_code` 与 `retry_after` 供节流层判断是否值得重试以及等待多久；
    `retryable` 为假表示客户端错误，重试没有意义。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.retryable = retryable


class RiskControlError(WKError):
    """触发平台风控（人机验证），需要人工介入后才能继续。"""

    def __init__(
        self,
        message: str,
        web_url: str | None = None,
        next_actions: list[NextAction] | None = None,
    ) -> None:
        super().__init__(message)
        self.web_url = web_url
        self.next_actions: list[NextAction] = next_actions or []


class OutputExistsError(WKError):
    """输出目标已存在，拒绝覆盖。"""


class ConfirmationRequiredError(WKError):
    """写操作缺少用户显式确认。"""

    def __init__(self, message: str, next_actions: list[NextAction] | None = None) -> None:
        super().__init__(message)
        self.next_actions: list[NextAction] = next_actions or []


class PartialFailureError(WKError):
    """批处理任务部分失败。"""

    def __init__(self, message: str, data: object = None) -> None:
        super().__init__(message)
        self.data: object = data
