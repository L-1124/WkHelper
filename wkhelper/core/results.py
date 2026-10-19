"""命令输出契约模型（信封 data 字段的强类型定义）。"""

from dataclasses import dataclass, field
from typing import Any

import msgspec

from wkhelper.core.entities import AccountSummary, StorageStatus
from wkhelper.core.json_types import JsonDict
from wkhelper.core.plan import _VIDEO_STATUSES, ExecutionSettings, VideoStatus, VideoTarget
from wkhelper.core.time_types import EpochMillis, now_millis


@dataclass(frozen=True)
class NextAction:
    """自引导下一步动作的强类型契约。"""

    reason: str
    argv: list[str]
    description: str = ""

    def to_dict(self) -> JsonDict:
        payload: JsonDict = {
            "reason": self.reason,
            "argv": list(self.argv),
        }
        if self.description:
            payload["description"] = self.description
        return payload


@dataclass
class CourseItem:
    id: str
    name: str
    platform: str
    classroom_id: int | str | None = None


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
    deadline: str | int | None = None
    completed: bool = False


@dataclass
class ArticleItem:
    id: str
    name: str
    completed: bool


@dataclass
class DiscussionItem:
    id: str
    name: str
    completed: bool


@dataclass
class OptionItem:
    key: str
    text: str


@dataclass
class QuestionSummary:
    index: int
    id: str
    type: str | None
    prompt: str
    content_truncated: bool
    options: list[OptionItem]
    submitted: bool
    is_correct: bool | None


@dataclass
class QuestionDetail:
    id: str
    type: str | None
    prompt: str
    options: list[OptionItem]
    submitted: bool
    user_answer: list[str] | str | None
    is_correct: bool | None
    score: str | float | None
    attempt_count: int
    library_id: int | str | None
    version: str | None
    resources: list[str] = field(default_factory=list)


@dataclass
class QuestionOutline:
    index: int
    id: str
    type: str | None
    library_id: int | str | None


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
class ArticleListData:
    articles: list[ArticleItem]


@dataclass
class ArticleReadResult:
    course_id: str
    leaf_id: str
    name: str
    success: bool
    completed: bool


@dataclass
class DiscussionListData:
    discussions: list[DiscussionItem]


@dataclass
class DiscussionTopicDetail:
    course_id: str
    leaf_id: str
    topic_id: int
    title: str
    prompt: str
    comment_count: int
    user_comment_num: int
    completed: bool


@dataclass
class DiscussionCommentResult:
    course_id: str
    leaf_id: str
    topic_id: int
    comment_id: int | None
    success: bool
    message: str


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
    resources_count: int = 0


@dataclass
class VideoWatchResult:
    """单视频学习结果。"""

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
    def from_mapping(cls, raw: Any, context: str = "检查点结果") -> "VideoTargetResult":
        """从检查点 JSON 值恢复结果，字段非法时抛出 ValueError。"""
        if not isinstance(raw, dict):
            raise ValueError(f"{context} 必须为 JSON 对象")
        try:
            res = msgspec.convert(raw, cls)
        except (msgspec.ValidationError, TypeError) as exc:
            raise ValueError(f"{context} 格式错误: {exc}") from exc
        if res.status not in _VIDEO_STATUSES:
            raise ValueError(f"{context} 字段 'status' 非法: {res.status}")
        return res


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
    error: str | None = None
    error_code: int | None = None


@dataclass
class QuestionSubmitResult:
    question_id: str
    submitted_answer: list[str]
    success: bool
    is_correct: bool
    correct_answer: list[str]
    error: str | None = None


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


@dataclass
class ExamItem:
    """考试节点简要信息。"""

    id: str
    name: str
    start_time: EpochMillis | None
    deadline: EpochMillis | None
    completed: bool
    overdue: bool = False

    def __post_init__(self) -> None:
        """未完成且已过截止时间即视为缺考。"""
        if not self.completed and not self.overdue and self.deadline:
            self.overdue = self.deadline < now_millis()


@dataclass
class ExamListData:
    exams: list[ExamItem]


@dataclass
class ExamCoverDetail:
    """考试封面元数据与考场须知详情。"""

    course_id: str
    exam_id: str
    title: str
    problem_count: int
    total_score: float
    limit_minutes: int
    start_time: EpochMillis | None
    deadline: EpochMillis | None
    status: str
    description: str
    face_auth: bool
    capture_screen: bool
    has_limit: bool = False
    app_capture_screen: bool = False
    front_random_snap: bool = False
    screen_cuts: bool = False
    max_screen_cuts: int = 0
    online_proctor: bool = False
    identity_auth: bool = False
    max_retry: int = 1
    is_offline: bool = False

    def __post_init__(self) -> None:
        """平台限时为 0 表示不限时（无倒计时），仅受截止时间约束。"""
        self.has_limit = self.limit_minutes > 0


@dataclass
class ExamStartPreview:
    """进入考场预检回执。"""

    dry_run: bool
    course_id: str
    exam_id: str
    title: str
    limit_minutes: int
    note: str
    notice: str = ""


@dataclass
class ExamStartResult:
    """进入考场正式回执。"""

    course_id: str
    exam_id: str
    title: str
    started: bool
    limit_minutes: int
    web_url: str
    message: str
    notice: str = ""
    browser_port: int | None = None


@dataclass
class ExamProblemItem:
    """考场试卷中的一道题目。"""

    index: int
    id: str
    type: str | None
    prompt: str
    images: list[str]
    options: list[OptionItem]
    score: float
    answer: list[str]
    has_remark: bool


@dataclass
class ExamPaperData:
    """考场试卷题目列表。"""

    course_id: str
    exam_id: str
    title: str
    problems: list[ExamProblemItem]


@dataclass
class ExamExportResult:
    """考场试卷导出结果。"""

    file_path: str
    exam_id: str
    problems_count: int
    resources_count: int


@dataclass
class ExamAnswerResult:
    """考试单题作答回执。"""

    dry_run: bool
    exam_id: str
    problem_id: str
    answer: list[str]
    saved: bool
    message: str
    notice: str = ""


@dataclass
class ExamSubmitResult:
    """考试交卷回执。"""

    dry_run: bool
    exam_id: str
    answered_count: int
    unanswered: list[str]
    submitted: bool
    message: str
    notice: str = ""


@dataclass
class ExamBrowserCloseResult:
    """考场浏览器会话关闭回执。"""

    exam_id: str
    closed: bool
    message: str
