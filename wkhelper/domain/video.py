"""视频数据。"""

from typing import Any, Literal

from pydantic import ConfigDict, Field

from wkhelper.domain.model import Model

type VideoStatus = Literal["completed", "threshold_reached", "timed_out", "stalled", "failed", "stopped"]


type ExecutionMode = Literal["stop", "continue"]


class Video(Model):
    """视频模型。"""

    id: int | str
    name: str
    completed: bool = False
    rate: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)  # 平台动态载荷


class VideoContext(Model):
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


class VideoItem(Model):
    id: str
    name: str
    completed: bool


class VideoProgress(Model):
    course_id: str
    video_id: str
    current_rate: float
    completed: bool
    unit_view_status: int | None = None
    lesson_view_status: int | None = None
    resume_position: float | None = None


class VideoListData(Model):
    videos: list[VideoItem]


class VideoWatchResult(Model):
    """单视频学习结果。"""

    model_config = ConfigDict(extra="ignore")

    video_id: str
    video_name: str = ""
    status: VideoStatus = "completed"
    platform_completed: bool = False
    initial_rate: float = 0.0
    final_rate: float = 0.0
    heartbeats_sent: int = 0
    learning_reports_sent: int = 0
    duration_seconds: float = 0.0
    message: str = ""

    def with_course_id(self, course_id: str) -> "VideoTargetResult":
        """附加课程 ID，保留全部观看结果字段。"""
        return VideoTargetResult.model_validate(self.model_dump() | {"course_id": course_id})


class VideoTargetResult(VideoWatchResult):
    """计划级单目标结果（含课程归属）。"""

    course_id: str = ""


class VideoPlanSummary(Model):
    total: int
    completed: int
    failed: int
    results: list[VideoTargetResult]


class VideoSnapshotFrame(Model):
    """单张视频抽帧元数据与落盘结果。"""

    timestamp: float
    actual_time: float | None = None
    file_path: str | None = None
    data_url: str | None = None
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    error: str | None = None


class VideoSnapshotData(Model):
    """视频批量截帧结果汇总。"""

    course_id: str
    video_id: str
    total_frames: int
    frames: list[VideoSnapshotFrame]
