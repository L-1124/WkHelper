"""JSON 编码统一边界。"""

from typing import Any, cast

from pydantic import JsonValue, TypeAdapter

_ADAPTER = TypeAdapter(Any)
_JSON_ADAPTER = TypeAdapter(JsonValue)


def encode_json(value: Any, *, indent: int | None = None) -> bytes:
    return _ADAPTER.dump_json(value, indent=indent, warnings="error")


def to_json_value(value: Any) -> JsonValue:
    return cast(JsonValue, _ADAPTER.dump_python(value, mode="json", warnings="error"))


def decode_json(value: str | bytes) -> JsonValue:
    return _JSON_ADAPTER.validate_json(value)
