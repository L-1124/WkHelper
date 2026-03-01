"""核心数据模型。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserInfo:
    """用户信息模型。"""

    id: int | str
    name: str
    school: str | None = None


@dataclass
class Course:
    """课程模型。"""

    id: int | str
    name: str
    platform_id: str  # 'ykt' 或 'xtzx'
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)  # 平台特定数据 (classroom_id, sku_id 等)


@dataclass
class Video:
    """视频模型。"""

    id: int | str
    name: str
    completed: bool = False
    rate: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Homework:
    """作业模型。"""

    id: int | str
    name: str
    deadline: float | None = None  # 时间戳
    completed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Question:
    """题目模型。"""

    id: int | str
    content: str
    options: list[str] = field(default_factory=list)
    answer: list[str] | str | None = None
    library_id: str | None = None
    version: str | None = None
