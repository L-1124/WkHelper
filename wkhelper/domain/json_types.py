"""JSON 值契约与宽松收窄工具。

容器使用协变协议（`Sequence` / `Mapping`），否则 `list[str]`、`dict[str, int]`
这类字面量会因容器不变性无法赋值，被迫在业务代码里到处 `cast`。
"""

import json
from collections.abc import Mapping, Sequence
from typing import cast

type JsonValue = str | int | float | bool | None | Sequence[JsonValue] | Mapping[str, JsonValue]


type JsonDict = dict[str, JsonValue]


def parse_json_text(text: str) -> JsonValue:
    """解析 JSON 文本为 `JsonValue`；`json.loads` 的 `Any` 在此边界一次性收窄。"""
    return cast(JsonValue, json.loads(text))


def json_float(value: JsonValue, default: float = 0.0) -> float:
    """把 JsonValue 收窄为 float，非数值返回默认值。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def json_int(value: JsonValue, default: int = 0) -> int:
    """把 JsonValue 收窄为 int，非整数返回默认值。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def json_str_list(value: JsonValue) -> list[str]:
    """把 JsonValue 收窄为字符串列表。"""
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and not isinstance(item, bool)]
    if isinstance(value, str):
        return [value]
    return []
