"""CLI 全局执行上下文与全局参数类型契约。"""

import sys
from typing import Literal

from wkhelper.infra.credentials import CredentialStore
from wkhelper.infra.events import set_debug_enabled

type OutputFormat = Literal["json", "md", "markdown"]
type CredentialMode = Literal["system", "session"]
type PlatformCode = Literal["ykt", "xtzx"]


class ExecutionContext:
    """进程级全局参数上下文，由 meta 启动器在解析完成后一次性写入。"""

    def __init__(self) -> None:
        self.format: OutputFormat | None = None
        self.data_dir: str | None = None
        self.credential_mode: CredentialMode = "system"
        self.cookie_file: str | None = None
        self.debug: bool = False
        self.user_agent: str | None = None
        self.proxy: str | None = None

    def configure(
        self,
        *,
        format: OutputFormat | None = None,
        data_dir: str | None = None,
        credential_mode: CredentialMode = "system",
        cookie_file: str | None = None,
        debug: bool = False,
        user_agent: str | None = None,
        proxy: str | None = None,
    ) -> None:
        """写入本次调用的全局参数，并同步调试日志开关。"""
        self.format = format
        self.data_dir = data_dir
        self.credential_mode = credential_mode
        self.cookie_file = cookie_file
        self.debug = debug
        self.user_agent = user_agent
        self.proxy = proxy
        set_debug_enabled(debug)

    def get_store(self) -> CredentialStore:
        """按全局参数构造凭据存储。"""
        return CredentialStore(data_dir=self.data_dir, credential_mode=self.credential_mode)


ctx = ExecutionContext()


def get_effective_format(is_terminal: bool | None = None) -> str:
    """计算当前生效的输出格式：显式参数优先；缺省时 TTY 默认为 md，非 TTY 默认为 json。"""
    if ctx.format is not None:
        return "md" if ctx.format.lower() in ("md", "markdown") else "json"
    if is_terminal is not None:
        return "md" if is_terminal else "json"
    is_atty = getattr(sys.stdout, "isatty", None)
    return "md" if is_atty and is_atty() else "json"


__all__ = [
    "CredentialMode",
    "ExecutionContext",
    "OutputFormat",
    "PlatformCode",
    "ctx",
    "get_effective_format",
]
