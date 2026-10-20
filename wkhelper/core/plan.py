"""视频学习计划与执行参数模型。"""

from dataclasses import dataclass
from typing import Any, Literal, cast

import msgspec

type VideoStatus = Literal["completed", "threshold_reached", "timed_out", "stalled", "failed", "stopped"]


type ExecutionMode = Literal["stop", "continue"]


_VIDEO_STATUSES: tuple[VideoStatus, ...] = ("completed", "threshold_reached", "timed_out", "stalled", "failed", "stopped")


def _reject_unknown(raw: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{context} 包含未知字段: {', '.join(unknown)}")


_PARAMETER_FIELDS: tuple[str, ...] = (
    "learning_rate",
    "heartbeat_interval",
    "jitter",
    "progress_interval",
    "completion_threshold",
    "video_timeout",
    "stall_timeout",
    "max_heartbeats",
    "video_delay",
)


@dataclass
class VideoParameters:
    """单视频策略参数；`None` 表示未指定，按优先级回退。"""

    learning_rate: float | None = None
    heartbeat_interval: float | None = None
    jitter: float | None = None
    progress_interval: float | None = None
    completion_threshold: float | None = None
    video_timeout: float | None = None
    stall_timeout: float | None = None
    max_heartbeats: int | None = None
    video_delay: float | None = None

    @classmethod
    def from_mapping(cls, raw: Any, context: str = "video_defaults") -> "VideoParameters":
        """从 JSON 对象解析参数，未知字段与非法取值一律报错。"""
        if not isinstance(raw, dict):
            raise ValueError(f"{context} 必须为 JSON 对象")
        _reject_unknown(raw, set(_PARAMETER_FIELDS), context)
        try:
            params = msgspec.convert(raw, cls)
        except (msgspec.ValidationError, TypeError) as exc:
            raise ValueError(f"{context} 参数格式错误: {exc}") from exc
        params.validate(context)
        return params

    def validate(self, context: str = "video_defaults") -> None:
        """校验已赋值字段的取值范围。"""
        if self.learning_rate is not None and self.learning_rate <= 0:
            raise ValueError(f"{context} 字段 'learning_rate' 必须为正数")
        if self.heartbeat_interval is not None and self.heartbeat_interval <= 0:
            raise ValueError(f"{context} 字段 'heartbeat_interval' 必须为正数")
        if self.jitter is not None and not 0 <= self.jitter < 1:
            raise ValueError(f"{context} 字段 'jitter' 必须位于 [0, 1) 区间")
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
        if self.video_delay is not None and self.video_delay < 0:
            raise ValueError(f"{context} 字段 'video_delay' 必须为非负数")

    def merge(self, lower: "VideoParameters | None") -> "VideoParameters":
        """合并低优先级参数：本对象已指定的字段优先（保留给调用方显式合并）。"""
        if lower is None:
            return self
        return VideoParameters(
            learning_rate=self.learning_rate if self.learning_rate is not None else lower.learning_rate,
            heartbeat_interval=self.heartbeat_interval if self.heartbeat_interval is not None else lower.heartbeat_interval,
            jitter=self.jitter if self.jitter is not None else lower.jitter,
            progress_interval=self.progress_interval if self.progress_interval is not None else lower.progress_interval,
            completion_threshold=(self.completion_threshold if self.completion_threshold is not None else lower.completion_threshold),
            video_timeout=self.video_timeout if self.video_timeout is not None else lower.video_timeout,
            stall_timeout=self.stall_timeout if self.stall_timeout is not None else lower.stall_timeout,
            max_heartbeats=self.max_heartbeats if self.max_heartbeats is not None else lower.max_heartbeats,
            video_delay=self.video_delay if self.video_delay is not None else lower.video_delay,
        )


@dataclass
class ExecutionSettings:
    """调度级配置（并发、错误策略、总预算）。"""

    concurrency: int = 1
    on_error: ExecutionMode = "stop"
    total_timeout: float = 3600.0

    @classmethod
    def from_mapping(cls, raw: Any, context: str = "execution") -> "ExecutionSettings":
        if not isinstance(raw, dict):
            raise ValueError(f"{context} 必须为 JSON 对象")
        _reject_unknown(raw, {"concurrency", "on_error", "total_timeout"}, context)
        try:
            settings = msgspec.convert(raw, cls)
        except (msgspec.ValidationError, TypeError) as exc:
            raise ValueError(f"{context} 格式错误: {exc}") from exc
        if settings.concurrency < 1:
            raise ValueError(f"{context} 字段 'concurrency' 必须为正整数")
        if settings.total_timeout <= 0:
            raise ValueError(f"{context} 字段 'total_timeout' 必须为正数")
        if settings.on_error not in ("stop", "continue"):
            raise ValueError(f"{context} 字段 'on_error' 仅支持 stop 或 continue")
        return settings


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
    def from_mapping(cls, raw: Any, context: str = "目标") -> "VideoTarget":
        if not isinstance(raw, dict):
            raise ValueError(f"{context} 必须为 JSON 对象")
        _reject_unknown(raw, {"course_id", "video_id", "name", "parameters"}, context)
        params_raw = raw.get("parameters")
        parameters = VideoParameters.from_mapping(params_raw, f"{context} parameters") if params_raw is not None else None
        course_id = raw.get("course_id")
        video_id = raw.get("video_id")
        if not isinstance(course_id, str) or not course_id.strip():
            raise ValueError(f"{context} 缺少有效的字符串字段 'course_id'")
        if not isinstance(video_id, str) or not video_id.strip():
            raise ValueError(f"{context} 缺少有效的字符串字段 'video_id'")
        name = raw.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError(f"{context} 字段 'name' 必须为字符串")
        return cls(course_id=course_id.strip(), video_id=video_id.strip(), name=name, parameters=parameters)


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
    def from_mapping(cls, raw: Any) -> "VideoPlan":
        """解析计划文件，未知字段与非法结构一律报错。"""
        if not isinstance(raw, dict):
            raise ValueError("计划文件根节点必须为 JSON 对象")
        _reject_unknown(raw, {"schema_version", "platform", "account", "execution", "video_defaults", "targets"}, "计划")

        version = raw.get("schema_version", 1)
        if not isinstance(version, int):
            raise ValueError("计划 字段 'schema_version' 必须为整数")
        if version != 1:
            raise ValueError(f"不支持的 schema_version: {version}")

        platform = raw.get("platform")
        if platform is not None and not isinstance(platform, str):
            raise ValueError("计划 字段 'platform' 必须为字符串")
        account = raw.get("account")
        if account is not None and not isinstance(account, str):
            raise ValueError("计划 字段 'account' 必须为字符串")

        execution_raw = raw.get("execution")
        execution = ExecutionSettings.from_mapping(execution_raw) if execution_raw is not None else ExecutionSettings()

        defaults_raw = raw.get("video_defaults")
        defaults = VideoParameters.from_mapping(defaults_raw) if defaults_raw is not None else VideoParameters()

        targets_raw = raw.get("targets")
        if not isinstance(targets_raw, list) or not targets_raw:
            raise ValueError("计划必须包含非空的 targets 数组")
        targets = [VideoTarget.from_mapping(item, f"计划第 {index} 个目标") for index, item in enumerate(targets_raw, 1)]

        return cls(
            platform=platform,
            account=account,
            execution=execution,
            video_defaults=defaults,
            targets=targets,
            schema_version=1,
        )
