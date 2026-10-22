"""视频学习计划与执行参数模型。"""

from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from wkhelper.domain.model import Model
from wkhelper.domain.video import ExecutionMode


class VideoParameters(Model):
    """None 表示未指定，按优先级回退。"""

    learning_rate: float | None = Field(default=None, gt=0)
    heartbeat_interval: float | None = Field(default=None, gt=0)
    jitter: float | None = Field(default=None, ge=0, lt=1)
    progress_interval: float | None = Field(default=None, gt=0)
    completion_threshold: float | None = Field(default=None, gt=0, le=1)
    video_timeout: float | None = Field(default=None, gt=0)
    stall_timeout: float | None = Field(default=None, gt=0)
    max_heartbeats: int | None = Field(default=None, ge=1)
    video_delay: float | None = Field(default=None, ge=0)

    def merge(self, lower: "VideoParameters | None") -> "VideoParameters":
        """已指定字段覆盖低优先级参数，合并后仍经完整校验。"""
        values = lower.model_dump(exclude_none=True) if lower is not None else {}
        return VideoParameters.model_validate(values | self.model_dump(exclude_none=True))


class ExecutionSettings(Model):
    concurrency: int = Field(default=1, ge=1)
    on_error: ExecutionMode = "stop"
    total_timeout: float = Field(default=3600.0, gt=0)


class ExecutionOverrides(Model):
    concurrency: int | None = Field(default=None, ge=1)
    on_error: ExecutionMode | None = None
    total_timeout: float | None = Field(default=None, gt=0)

    def overlay(self, base: ExecutionSettings) -> ExecutionSettings:
        return ExecutionSettings.model_validate(base.model_dump() | self.model_dump(exclude_none=True))


type TargetId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class VideoTarget(Model):
    course_id: TargetId
    video_id: TargetId
    name: str | None = None
    parameters: VideoParameters | None = None


class VideoPlan(Model):
    platform: str | None = None
    account: str | None = None
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    video_defaults: VideoParameters = Field(default_factory=VideoParameters)
    targets: list[VideoTarget] = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1, le=1)

    @field_validator("execution", "video_defaults", mode="before")
    @classmethod
    def null_uses_default(cls, value: object) -> object:
        return {} if value is None else value


class VideoLearnPreview(Model):
    dry_run: bool
    targets_count: int
    targets: list[VideoTarget]
    requested_config: ExecutionSettings
    effective_config: ExecutionSettings
    note: str
