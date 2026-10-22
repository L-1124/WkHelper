"""视频数据。"""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

type VideoStatus = Literal["completed", "threshold_reached", "timed_out", "stalled", "failed", "stopped"]


type ExecutionMode = Literal["stop", "continue"]


@dataclass
class Video:
    """视频模型。"""

    id: int | str
    name: str
    completed: bool = False
    rate: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)  # 平台动态载荷


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
class VideoItem:
    id: str
    name: str
    completed: bool


@dataclass
class VideoProgress:
    course_id: str
    video_id: str
    current_rate: float
    completed: bool


@dataclass
class VideoListData:
    videos: list[VideoItem]


class VideoWatchResult(BaseModel):
    """单视频学习结果。"""

    model_config = ConfigDict(strict=True, allow_inf_nan=False, validate_default=True, validate_assignment=True, extra="ignore")

    video_id: str
    video_name: str = ""
    status: VideoStatus = "completed"
    platform_completed: bool = False
    initial_rate: float = 0.0
    final_rate: float = 0.0
    heartbeats_sent: int = 0
    duration_seconds: float = 0.0
    message: str = ""

    def with_course_id(self, course_id: str) -> "VideoTargetResult":
        """附加课程 ID，保留全部观看结果字段。"""
        return VideoTargetResult.model_validate(self.model_dump() | {"course_id": course_id})


class VideoTargetResult(VideoWatchResult):
    """计划级单目标结果（含课程归属）。"""

    course_id: str = ""


@dataclass
class VideoPlanSummary:
    total: int
    completed: int
    failed: int
    results: list[VideoTargetResult]


@dataclass
class VideoSnapshotFrame:
    """单张视频抽帧元数据与落盘结果。"""

    timestamp: float
    file_path: str | None = None
    data_url: str | None = None
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    error: str | None = None


@dataclass
class VideoSnapshotData:
    """视频批量截帧结果汇总。"""

    course_id: str
    video_id: str
    total_frames: int
    frames: list[VideoSnapshotFrame]
