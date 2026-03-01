"""wkhelper 核心异常定义。"""


class WKError(Exception):
    """wkhelper 基础异常。"""


class AuthError(WKError):
    """身份验证失败。"""


class APIError(WKError):
    """API 请求失败。"""


class NetworkError(WKError):
    """网络连接失败。"""


class PlatformError(WKError):
    """平台特定逻辑错误。"""
