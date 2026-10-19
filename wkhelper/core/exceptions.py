"""wkhelper 核心异常定义（强类型 NextAction 约束）。"""

from wkhelper.core.results import NextAction


class WKError(Exception):
    """wkhelper 基础异常。"""


class AuthError(WKError):
    """身份验证失败。"""


class APIError(WKError):
    """API 请求失败。"""


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
