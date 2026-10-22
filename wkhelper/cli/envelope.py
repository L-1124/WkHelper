"""统一输出信封与强类型结果模型（JSON 值契约）。"""

import sys
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, JsonValue

from wkhelper.domain.actions import NextAction
from wkhelper.domain.model import Model
from wkhelper.infra.serialization import encode_json

SCHEMA_VERSION = 1


class ErrorDetail(Model):
    """结构化错误信息。"""

    code: str
    message: str
    retryable: bool = False
    details: dict[str, JsonValue] = Field(default_factory=dict)


class CommandResult[T](Model):
    """命令函数返回的领域业务结果，与展现层完全解耦。"""

    data: T
    meta: dict[str, JsonValue] | None = None
    next_actions: list[NextAction] = Field(default_factory=list)
    exit_code: int = 0


class SuccessEnvelope[T](Model):
    """强类型成功响应信封。"""

    schema_version: int = SCHEMA_VERSION
    ok: Literal[True] = True
    data: T
    meta: dict[str, JsonValue] | None = Field(default=None, exclude_if=lambda v: v is None)
    next_actions: list[NextAction] = Field(default_factory=list)


class ErrorEnvelope(Model):
    """强类型错误响应信封。"""

    schema_version: int = SCHEMA_VERSION
    ok: Literal[False] = False
    error: ErrorDetail
    data: Any = None
    next_actions: list[NextAction] = Field(default_factory=list)


def create_success_envelope[T](
    data: T,
    meta: dict[str, JsonValue] | None = None,
    next_actions: list[NextAction] | None = None,
) -> dict[str, Any]:
    """生成成功响应信封。"""
    envelope = SuccessEnvelope(
        data=data,
        meta=meta,
        next_actions=next_actions or [],
    )
    return envelope.model_dump()


def create_error_envelope(
    code: str,
    message: str,
    retryable: bool = False,
    details: dict[str, JsonValue] | None = None,
    data: Any = None,
    next_actions: list[NextAction] | None = None,
) -> dict[str, Any]:
    """生成错误响应信封。"""
    envelope = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, retryable=retryable, details=details or {}),
        data=data,
        next_actions=next_actions or [],
    )
    return envelope.model_dump()


def print_json(envelope: Mapping[str, Any]) -> None:
    """向标准输出打印单行 JSON 字符串并刷新。

    统一编码支持 dataclass、输入模型、嵌套结构与 UTF-8 中文，
    遇到不可序列化类型直接抛错，不会输出半截 JSON。
    """
    sys.stdout.write(encode_json(envelope).decode("utf-8") + "\n")
    sys.stdout.flush()
