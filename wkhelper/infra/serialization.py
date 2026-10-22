"""JSON 编码统一边界。"""

from typing import Any, cast

from pydantic import TypeAdapter

from wkhelper.domain.json_types import JsonValue

_ADAPTER = TypeAdapter(Any)


def encode_json(value: Any, *, indent: int | None = None) -> bytes:
    return _ADAPTER.dump_json(value, indent=indent, warnings="error")


def to_json_value(value: Any) -> JsonValue:
    return cast(JsonValue, _ADAPTER.dump_python(value, mode="json", warnings="error"))
