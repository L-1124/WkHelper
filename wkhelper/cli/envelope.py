"""统一输出信封与强类型结果模型（JSON 值契约，msgspec 编码）。"""

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import msgspec

from wkhelper.core.models import JsonDict

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class NextAction:
    """自引导下一步动作的强类型契约。"""

    reason: str
    argv: list[str]
    description: str = ""

    def to_dict(self) -> JsonDict:
        payload: JsonDict = {
            "reason": self.reason,
            "argv": list(self.argv),
        }
        if self.description:
            payload["description"] = self.description
        return payload


@dataclass
class ErrorDetail:
    """结构化错误信息。"""

    code: str
    message: str
    retryable: bool = False
    details: JsonDict = field(default_factory=dict)


@dataclass
class CommandResult[T]:
    """命令函数返回的领域业务结果，与展现层完全解耦。"""

    data: T
    meta: JsonDict | None = None
    next_actions: list[NextAction] = field(default_factory=list)
    exit_code: int = 0


@dataclass
class SuccessEnvelope[T]:
    """强类型成功响应信封。"""

    data: T
    schema_version: int = SCHEMA_VERSION
    ok: bool = True
    meta: JsonDict | None = None
    next_actions: list[NextAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "ok": True,
            "data": self.data,
        }
        if self.meta is not None:
            payload["meta"] = self.meta
        payload["next_actions"] = [action.to_dict() for action in self.next_actions]
        return payload


@dataclass
class ErrorEnvelope:
    """强类型错误响应信封。"""

    error: ErrorDetail
    schema_version: int = SCHEMA_VERSION
    ok: bool = False
    data: Any = None
    next_actions: list[NextAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ok": False,
            "error": {
                "code": self.error.code,
                "message": self.error.message,
                "retryable": self.error.retryable,
                "details": self.error.details,
            },
            "data": self.data,
            "next_actions": [action.to_dict() for action in self.next_actions],
        }


def create_success_envelope[T](
    data: T,
    meta: JsonDict | None = None,
    next_actions: list[NextAction] | None = None,
) -> dict[str, Any]:
    """生成成功响应信封。"""
    envelope = SuccessEnvelope(
        data=data,
        meta=meta,
        next_actions=next_actions or [],
    )
    return envelope.to_dict()


def create_error_envelope(
    code: str,
    message: str,
    retryable: bool = False,
    details: JsonDict | None = None,
    data: Any = None,
    next_actions: list[NextAction] | None = None,
) -> dict[str, Any]:
    """生成错误响应信封。"""
    envelope = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, retryable=retryable, details=details or {}),
        data=data,
        next_actions=next_actions or [],
    )
    return envelope.to_dict()


def print_json(envelope: Mapping[str, Any]) -> None:
    """向标准输出打印单行 JSON 字符串并刷新。

    使用 msgspec 编码：原生支持 dataclass、嵌套结构与 UTF-8 中文，
    遇到不可序列化类型直接抛错，不会输出半截 JSON。
    """
    sys.stdout.write(msgspec.json.encode(envelope).decode("utf-8") + "\n")
    sys.stdout.flush()
