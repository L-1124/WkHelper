"""平台领域实体（跨平台通用的课程、作业、题目、账号等模型）。"""

from dataclasses import dataclass, field
from typing import Any

from wkhelper.core.json_types import JsonDict


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
    metadata: dict[str, Any] = field(default_factory=dict)  # 平台特定数据 (classroom_id, sku_id 等)，动态载荷


def course_reference(course: Course) -> str:
    """生成对外稳定的课程引用：原生课程 ID + 课堂 ID。

    同一门慕课课程常同时挂在主班与子班两个课堂下（course.id 相同、classroom_id 不同），
    而章节内容按课堂区分。只使用原生课程 ID 会让两个课堂互相顶掉，因此对外引用必须
    包含课堂信息；没有课堂概念的平台退回原生 ID。
    """
    classroom_id = course.metadata.get("classroom_id")
    if classroom_id is None:
        return str(course.id)
    return f"{course.id}:{classroom_id}"


def dedupe_courses(courses: list[Course]) -> list[Course]:
    """按课程引用去重，保留每个课堂首次出现的条目。

    避免同一课堂重复出现，同时保留同一课程的不同课堂（子班往往没有章节）。
    """
    seen: dict[str, Course] = {}
    for course in courses:
        seen.setdefault(course_reference(course), course)
    return list(seen.values())


@dataclass
class Video:
    """视频模型。"""

    id: int | str
    name: str
    completed: bool = False
    rate: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)  # 平台动态载荷


@dataclass
class Homework:
    """作业模型。"""

    id: int | str
    name: str
    deadline: str | int | None = None
    completed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)  # 平台动态载荷


@dataclass
class Question:
    """题目模型。"""

    id: int | str
    content: str
    options: list[str] = field(default_factory=list)
    answer: list[str] | str | None = None
    library_id: str | None = None
    version: str | None = None


@dataclass
class VideoContext:
    """视频观看所需的平台上下文。"""

    video_id: str
    classroom_id: str
    user_id: int
    course_id: int
    sku_id: int
    progress_url: str
    heartbeat_url: str
    progress_params: dict[str, Any]  # 平台 query 参数，动态载荷
    request_kwargs: dict[str, Any] | None = None  # HTTP 附加参数，动态边界
    media_ccid: str = ""  # CDN 存储 ID，心跳 cc 字段
    duration: float = 0.0  # 视频真实时长，心跳 d 字段


@dataclass
class AccountSummary:
    """账号元数据摘要（不含秘密）。"""

    platform: str
    user_id: str
    name: str
    school: str | None
    saved_at: float


@dataclass
class StorageStatus:
    """系统凭据存储后端状态。"""

    mode: str
    backend: str
    available: bool
    priority: int = 0
    persisted: bool = True
    data_dir: str = ""
    metadata_db: str = ""


@dataclass
class PaginationMeta:
    """列表分页元数据。"""

    limit: int
    offset: int
    total: int
    has_more: bool
    next_cursor: str | None
    pagination_mode: str = "local"

    def to_dict(self) -> JsonDict:
        return {
            "limit": self.limit,
            "offset": self.offset,
            "total": self.total,
            "has_more": self.has_more,
            "next_cursor": self.next_cursor,
            "pagination_mode": self.pagination_mode,
        }
