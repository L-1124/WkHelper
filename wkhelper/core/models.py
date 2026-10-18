"""核心数据模型与 JSON 值类型。

类型策略：
- 输出/编排层（信封、事件、Schema、命令结果、调度摘要）全部使用精确模型与
  `JsonValue` / `JsonDict`，不出现 `Any`。
- 平台原始载荷（HTTP JSON、章节树、字体结构）保持 `dict[str, Any]`，
  这是必要且已接受的动态边界，集中在 platform 适配层。
- 编码统一交给 `msgspec.json.encode`（原生支持 dataclass 与嵌套结构），
  本模块只负责类型契约与宽松/严格收窄工具。
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

type JsonValue = str | int | float | bool | None | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
type JsonDict = dict[str, JsonValue]
type VideoStatus = Literal["completed", "threshold_reached", "timed_out", "stalled", "failed", "stopped"]
type ExecutionMode = Literal["stop", "continue"]

_VIDEO_STATUSES: tuple[VideoStatus, ...] = ("completed", "threshold_reached", "timed_out", "stalled", "failed", "stopped")


def parse_json_text(text: str) -> JsonValue:
    """解析 JSON 文本为 JsonValue，取代裸 `json.loads` 的 Any 返回值。"""
    return json.loads(text)


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


def json_str(value: JsonValue, default: str = "") -> str:
    """把 JsonValue 收窄为 str。"""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return str(value)
    return default


def json_str_list(value: JsonValue) -> list[str]:
    """把 JsonValue 收窄为字符串列表。"""
    if isinstance(value, list):
        return [json_str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def _require_str(raw: dict[str, JsonValue], key: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} 缺少有效的字符串字段 '{key}'")
    return value


def _optional_str(raw: dict[str, JsonValue], key: str, context: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{context} 字段 '{key}' 必须为字符串")
    return value


def _optional_bool(raw: dict[str, JsonValue], key: str, context: str, default: bool = False) -> bool:
    value = raw.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{context} 字段 '{key}' 必须为布尔值")
    return value


def _optional_float(raw: dict[str, JsonValue], key: str, context: str) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} 字段 '{key}' 必须为数值")
    return float(value)


def _optional_int(raw: dict[str, JsonValue], key: str, context: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context} 字段 '{key}' 必须为整数")
    return value


def _reject_unknown(raw: dict[str, JsonValue], allowed: set[str], context: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{context} 包含未知字段: {', '.join(unknown)}")


# 领域模型


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
    deadline: JsonValue = None  # 平台原始截止时间值（未归一化）
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


# 输出契约模型


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


@dataclass
class CourseItem:
    id: str
    name: str
    platform: str
    classroom_id: JsonValue = None


@dataclass
class CourseDetail:
    id: str
    name: str
    platform: str
    metadata: JsonDict


@dataclass
class VideoItem:
    id: str
    name: str
    completed: bool


@dataclass
class HomeworkItem:
    id: str
    name: str
    deadline: JsonValue = None
    completed: bool = False


@dataclass
class OptionItem:
    key: JsonValue
    text: str


@dataclass
class QuestionSummary:
    index: int
    id: str
    type: JsonValue
    prompt: str
    content_truncated: bool
    options: list[OptionItem]
    submitted: bool
    is_correct: JsonValue


@dataclass
class QuestionDetail:
    id: str
    type: JsonValue
    prompt: str
    options: list[OptionItem]
    submitted: bool
    user_answer: JsonValue
    is_correct: JsonValue
    score: JsonValue
    attempt_count: int
    library_id: JsonValue
    version: JsonValue


@dataclass
class QuestionOutline:
    index: int
    id: str
    type: JsonValue
    library_id: JsonValue


@dataclass
class HomeworkOutline:
    course_id: str
    homework_id: str
    total_questions: int
    questions_summary: list[QuestionOutline]


@dataclass
class VideoProgress:
    course_id: str
    video_id: str
    current_rate: float
    completed: bool


@dataclass
class StatusData:
    credential_storage: StorageStatus
    accounts_count: int
    accounts: list[AccountSummary]


@dataclass
class AccountListData:
    accounts: list[AccountSummary]


@dataclass
class CourseListData:
    courses: list[CourseItem]


@dataclass
class VideoListData:
    videos: list[VideoItem]


@dataclass
class HomeworkListData:
    homeworks: list[HomeworkItem]


@dataclass
class QuestionListData:
    questions: list[QuestionSummary]


@dataclass
class AuthStatusResult:
    platform: str
    user_id: str
    name: str
    valid: bool


@dataclass
class LoginResult:
    platform: str
    user_id: str
    name: str
    school: str | None
    persisted: bool


@dataclass
class RemoveResult:
    platform: str
    account: str
    deleted: bool


@dataclass
class OutputFileResult:
    output_file: str
    written: bool


@dataclass
class ExportResult:
    file_path: str | None
    homeworks_count: int
    questions_count: int


# 执行结果模型


@dataclass
class VideoWatchResult:
    """单视频学习结果。"""

    video_id: str
    video_name: str
    status: VideoStatus
    platform_completed: bool
    initial_rate: float
    final_rate: float
    heartbeats_sent: int
    duration_seconds: float
    message: str

    def with_course_id(self, course_id: str) -> "VideoTargetResult":
        """附加课程 ID，转换为计划级结果。"""
        return VideoTargetResult(
            video_id=self.video_id,
            video_name=self.video_name,
            status=self.status,
            platform_completed=self.platform_completed,
            initial_rate=self.initial_rate,
            final_rate=self.final_rate,
            heartbeats_sent=self.heartbeats_sent,
            duration_seconds=self.duration_seconds,
            message=self.message,
            course_id=course_id,
        )


@dataclass
class VideoTargetResult(VideoWatchResult):
    """计划级单目标结果（含课程归属）。"""

    course_id: str = ""

    @classmethod
    def from_mapping(cls, raw: JsonValue, context: str = "检查点结果") -> "VideoTargetResult":
        """从检查点 JSON 值恢复结果，字段非法时抛出 ValueError。"""
        if not isinstance(raw, dict):
            raise ValueError(f"{context} 必须为 JSON 对象")
        status_text = _require_str(raw, "status", context)
        if status_text not in _VIDEO_STATUSES:
            raise ValueError(f"{context} 字段 'status' 非法: {status_text}")
        rate = _optional_float(raw, "final_rate", context)
        return cls(
            course_id=_require_str(raw, "course_id", context),
            video_id=_require_str(raw, "video_id", context),
            video_name=_optional_str(raw, "video_name", context) or "",
            status=cast(VideoStatus, status_text),
            platform_completed=_optional_bool(raw, "platform_completed", context),
            initial_rate=_optional_float(raw, "initial_rate", context) or 0.0,
            final_rate=rate if rate is not None else 0.0,
            heartbeats_sent=_optional_int(raw, "heartbeats_sent", context) or 0,
            duration_seconds=_optional_float(raw, "duration_seconds", context) or 0.0,
            message=_optional_str(raw, "message", context) or "",
        )


@dataclass
class VideoPlanSummary:
    total: int
    completed: int
    failed: int
    results: list[VideoTargetResult]


@dataclass
class SubmitItem:
    index: int
    question_id: str
    submitted: bool
    is_correct: bool
    correct_answer: list[str] | None = None
    error: str | None = None


@dataclass
class HomeworkSubmitResult:
    total: int
    submitted_count: int
    correct_count: int
    items: list[SubmitItem]


@dataclass
class QuestionSubmitOutcome:
    """平台单题提交回执。"""

    success: bool
    is_correct: bool
    correct_answer: list[str]


@dataclass
class QuestionSubmitResult:
    question_id: str
    submitted_answer: list[str]
    success: bool
    is_correct: bool
    correct_answer: list[str]


# 执行参数与计划模型

_PARAMETER_FIELDS: tuple[str, ...] = (
    "learning_rate",
    "heartbeat_interval",
    "progress_interval",
    "completion_threshold",
    "video_timeout",
    "stall_timeout",
    "max_heartbeats",
)


@dataclass
class VideoParameters:
    """单视频策略参数；`None` 表示未指定，按优先级回退。"""

    learning_rate: float | None = None
    heartbeat_interval: float | None = None
    progress_interval: float | None = None
    completion_threshold: float | None = None
    video_timeout: float | None = None
    stall_timeout: float | None = None
    max_heartbeats: int | None = None

    @classmethod
    def from_mapping(cls, raw: JsonValue, context: str = "video_defaults") -> "VideoParameters":
        """从 JSON 对象解析参数，未知字段与非法取值一律报错。"""
        if not isinstance(raw, dict):
            raise ValueError(f"{context} 必须为 JSON 对象")
        _reject_unknown(raw, set(_PARAMETER_FIELDS), context)

        learning_rate = _optional_float(raw, "learning_rate", context)
        heartbeat_interval = _optional_float(raw, "heartbeat_interval", context)
        progress_interval = _optional_float(raw, "progress_interval", context)
        completion_threshold = _optional_float(raw, "completion_threshold", context)
        video_timeout = _optional_float(raw, "video_timeout", context)
        stall_timeout = _optional_float(raw, "stall_timeout", context)
        max_heartbeats = _optional_int(raw, "max_heartbeats", context)

        parameters = cls(
            learning_rate=learning_rate,
            heartbeat_interval=heartbeat_interval,
            progress_interval=progress_interval,
            completion_threshold=completion_threshold,
            video_timeout=video_timeout,
            stall_timeout=stall_timeout,
            max_heartbeats=max_heartbeats,
        )
        parameters.validate(context)
        return parameters

    def validate(self, context: str = "video_defaults") -> None:
        """校验已赋值字段的取值范围。"""
        if self.learning_rate is not None and self.learning_rate <= 0:
            raise ValueError(f"{context} 字段 'learning_rate' 必须为正数")
        if self.heartbeat_interval is not None and self.heartbeat_interval <= 0:
            raise ValueError(f"{context} 字段 'heartbeat_interval' 必须为正数")
        if self.progress_interval is not None and self.progress_interval <= 0:
            raise ValueError(f"{context} 字段 'progress_interval' 必须为正数")
        if self.video_timeout is not None and self.video_timeout <= 0:
            raise ValueError(f"{context} 字段 'video_timeout' 必须为正数")
        if self.stall_timeout is not None and self.stall_timeout <= 0:
            raise ValueError(f"{context} 字段 'stall_timeout' 必须为正数")
        if self.completion_threshold is not None and not 0 < self.completion_threshold <= 1:
            raise ValueError(f"{context} 字段 'completion_threshold' 必须位于 (0, 1] 区间")
        if self.max_heartbeats is not None and self.max_heartbeats < 1:
            raise ValueError(f"{context} 字段 'max_heartbeats' 必须为正整数")

    def merge(self, lower: "VideoParameters | None") -> "VideoParameters":
        """合并低优先级参数：本对象已指定的字段优先（保留给调用方显式合并）。"""
        if lower is None:
            return self
        return VideoParameters(
            learning_rate=self.learning_rate if self.learning_rate is not None else lower.learning_rate,
            heartbeat_interval=self.heartbeat_interval if self.heartbeat_interval is not None else lower.heartbeat_interval,
            progress_interval=self.progress_interval if self.progress_interval is not None else lower.progress_interval,
            completion_threshold=(self.completion_threshold if self.completion_threshold is not None else lower.completion_threshold),
            video_timeout=self.video_timeout if self.video_timeout is not None else lower.video_timeout,
            stall_timeout=self.stall_timeout if self.stall_timeout is not None else lower.stall_timeout,
            max_heartbeats=self.max_heartbeats if self.max_heartbeats is not None else lower.max_heartbeats,
        )


@dataclass
class ExecutionSettings:
    """调度级配置（并发、错误策略、总预算）。"""

    concurrency: int = 1
    on_error: ExecutionMode = "stop"
    total_timeout: float = 3600.0

    @classmethod
    def from_mapping(cls, raw: JsonValue, context: str = "execution") -> "ExecutionSettings":
        if not isinstance(raw, dict):
            raise ValueError(f"{context} 必须为 JSON 对象")
        _reject_unknown(raw, {"concurrency", "on_error", "total_timeout"}, context)
        concurrency = _optional_int(raw, "concurrency", context)
        on_error = _optional_str(raw, "on_error", context)
        total_timeout = _optional_float(raw, "total_timeout", context)
        if concurrency is not None and concurrency < 1:
            raise ValueError(f"{context} 字段 'concurrency' 必须为正整数")
        if total_timeout is not None and total_timeout <= 0:
            raise ValueError(f"{context} 字段 'total_timeout' 必须为正数")
        if on_error is not None and on_error not in ("stop", "continue"):
            raise ValueError(f"{context} 字段 'on_error' 仅支持 stop 或 continue")
        return cls(
            concurrency=concurrency if concurrency is not None else 1,
            on_error=cast(ExecutionMode, on_error if on_error is not None else "stop"),
            total_timeout=total_timeout if total_timeout is not None else 3600.0,
        )


@dataclass
class ExecutionOverrides:
    """命令行显式覆盖项；`None` 表示未显式指定。"""

    concurrency: int | None = None
    on_error: str | None = None
    total_timeout: float | None = None

    def overlay(self, base: ExecutionSettings) -> ExecutionSettings:
        """叠加在计划级配置之上，显式字段优先。"""
        if self.on_error is not None and self.on_error not in ("stop", "continue"):
            raise ValueError("参数 'on_error' 仅支持 stop 或 continue")
        if self.concurrency is not None and self.concurrency < 1:
            raise ValueError("参数 'concurrency' 必须为正整数")
        if self.total_timeout is not None and self.total_timeout <= 0:
            raise ValueError("参数 'total_timeout' 必须为正数")
        return ExecutionSettings(
            concurrency=self.concurrency if self.concurrency is not None else base.concurrency,
            on_error=cast(ExecutionMode, self.on_error if self.on_error is not None else base.on_error),
            total_timeout=self.total_timeout if self.total_timeout is not None else base.total_timeout,
        )


@dataclass
class VideoTarget:
    """显式视频目标。"""

    course_id: str
    video_id: str
    name: str | None = None
    parameters: VideoParameters | None = None

    @classmethod
    def from_mapping(cls, raw: JsonValue, context: str = "目标") -> "VideoTarget":
        if not isinstance(raw, dict):
            raise ValueError(f"{context} 必须为 JSON 对象")
        _reject_unknown(raw, {"course_id", "video_id", "name", "parameters"}, context)
        raw_params = raw.get("parameters")
        parameters = VideoParameters.from_mapping(raw_params, f"{context} parameters") if raw_params is not None else None
        return cls(
            course_id=_require_str(raw, "course_id", context),
            video_id=_require_str(raw, "video_id", context),
            name=_optional_str(raw, "name", context),
            parameters=parameters,
        )


@dataclass
class VideoPlan:
    """Agent 编写的显式执行计划。"""

    platform: str | None
    account: str | None
    execution: ExecutionSettings
    video_defaults: VideoParameters
    targets: list[VideoTarget]
    schema_version: int = 1

    @classmethod
    def from_mapping(cls, raw: JsonValue) -> "VideoPlan":
        """解析计划文件，未知字段与非法结构一律报错。"""
        if not isinstance(raw, dict):
            raise ValueError("计划文件根节点必须为 JSON 对象")
        _reject_unknown(raw, {"schema_version", "platform", "account", "execution", "video_defaults", "targets"}, "计划")

        version = _optional_int(raw, "schema_version", "计划")
        if version is not None and version != 1:
            raise ValueError(f"不支持的 schema_version: {version}")

        execution_raw = raw.get("execution")
        execution = ExecutionSettings.from_mapping(execution_raw) if execution_raw is not None else ExecutionSettings()

        defaults_raw = raw.get("video_defaults")
        defaults = VideoParameters.from_mapping(defaults_raw) if defaults_raw is not None else VideoParameters()

        targets_raw = raw.get("targets")
        if not isinstance(targets_raw, list) or not targets_raw:
            raise ValueError("计划必须包含非空的 targets 数组")
        targets = [VideoTarget.from_mapping(item, f"计划第 {index} 个目标") for index, item in enumerate(targets_raw, 1)]

        return cls(
            platform=_optional_str(raw, "platform", "计划"),
            account=_optional_str(raw, "account", "计划"),
            execution=execution,
            video_defaults=defaults,
            targets=targets,
            schema_version=1,
        )


# 预览模型


@dataclass
class VideoLearnPreview:
    dry_run: bool
    targets_count: int
    targets: list[VideoTarget]
    requested_config: ExecutionSettings
    effective_config: ExecutionSettings
    note: str


@dataclass
class HomeworkSubmitPreview:
    dry_run: bool
    course_id: str
    homework_id: str
    answers_count: int
    validated: bool


@dataclass
class QuestionSubmitPreview:
    dry_run: bool
    course_id: str
    homework_id: str
    question_id: str
    answer: list[str]
