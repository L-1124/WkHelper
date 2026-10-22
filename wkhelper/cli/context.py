"""调用级执行上下文与不可变会话参数。"""

import sys
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Annotated, Literal, Self

from cyclopts import Parameter
from pydantic import ConfigDict, model_validator

from wkhelper.domain.model import Model
from wkhelper.infra.credentials import CredentialStore
from wkhelper.infra.events import debug_scope

type OutputFormat = Literal["json", "md", "markdown"]
type CredentialMode = Literal["system", "session"]
type PlatformCode = Literal["ykt", "xtzx"]


class SessionOptions(Model):
    """CLI 与嵌入调用共享的会话配置契约。"""

    model_config = ConfigDict(frozen=True)

    format: Annotated[OutputFormat | None, Parameter(help="输出格式：json 或 md（缺省按 TTY 推断）")] = None
    data_dir: Annotated[str | None, Parameter(help="凭据与检查点数据目录")] = None
    credential_mode: Annotated[CredentialMode, Parameter(help="凭据存储模式")] = "system"
    cookie_file: Annotated[str | None, Parameter(help="会话 Cookie 文件（需配合 `--credential-mode session`）")] = None
    debug: Annotated[bool, Parameter(help="输出调试堆栈与 NDJSON 事件")] = False
    user_agent: Annotated[str | None, Parameter(help="覆盖请求 User-Agent")] = None
    proxy: Annotated[str | None, Parameter(help="HTTP 代理地址")] = None

    @model_validator(mode="after")
    def validate_cookie_mode(self) -> Self:
        if self.cookie_file is not None and self.credential_mode != "session":
            raise ValueError("全局 `--cookie-file` 仅支持与 `--credential-mode session` 搭配使用")
        return self


class ExecutionContext:
    """运行时状态与配置分离，生命周期由入口持有。"""

    def __init__(self, options: SessionOptions) -> None:
        self.options = options

    def get_store(self) -> CredentialStore:
        """保持按需创建凭据存储的生命周期。"""
        return CredentialStore(data_dir=self.options.data_dir, credential_mode=self.options.credential_mode)


_current: ContextVar[ExecutionContext] = ContextVar("execution_context")


def get_context() -> ExecutionContext:
    """未进入调用作用域时拒绝隐式使用默认配置。"""
    return _current.get()


@contextmanager
def execution_context(options: SessionOptions | None = None) -> Generator[ExecutionContext]:
    """隔离并发调用，退出时恢复外层上下文和调试状态。"""
    context = ExecutionContext(options if options is not None else SessionOptions())
    token = _current.set(context)
    try:
        with debug_scope(context.options.debug):
            yield context
    finally:
        _current.reset(token)


def get_effective_format(is_terminal: bool | None = None) -> str:
    """计算当前生效的输出格式：显式参数优先；缺省时 TTY 默认为 md，非 TTY 默认为 json。"""
    format = get_context().options.format
    if format is not None:
        return "md" if format in ("md", "markdown") else "json"
    if is_terminal is not None:
        return "md" if is_terminal else "json"
    is_atty = getattr(sys.stdout, "isatty", None)
    return "md" if is_atty and is_atty() else "json"


__all__ = [
    "CredentialMode",
    "ExecutionContext",
    "OutputFormat",
    "PlatformCode",
    "SessionOptions",
    "execution_context",
    "get_context",
    "get_effective_format",
]
