"""考试数据。"""

from dataclasses import dataclass

from wkhelper.domain.homework import OptionItem
from wkhelper.domain.time_types import EpochMillis, now_millis


@dataclass
class ExamItem:
    """考试节点简要信息。"""

    id: str
    name: str
    start_time: EpochMillis | None
    deadline: EpochMillis | None
    completed: bool
    overdue: bool = False
    status: str = ""

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
    raw_status: int | None = None

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
class ExamBrowserCloseResult:
    """考场浏览器会话关闭回执。"""

    exam_id: str
    closed: bool
    message: str


@dataclass
class ExamResultData:
    """考场成绩与作答结果概览。"""

    exam_id: str
    grade: float | None
    duration: int | None
    problem_count: int
    message: str = ""


@dataclass
class ExamStatusData:
    """考场服务端状态快照（只读诊断）。"""

    exam_id: str
    room_status: str | None
    room_status_raw: int | None
    lms_status: int | None
    refresh_code: int | None
    refresh_message: str
    cached_count: int
    cached_sample: str
    result_code: int | None
    result_message: str
