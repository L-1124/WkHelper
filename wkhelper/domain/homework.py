"""作业与题目数据。"""

from dataclasses import dataclass, field
from typing import Any


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
class HomeworkItem:
    id: str
    name: str
    deadline: str | int | None = None
    completed: bool = False


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
class HomeworkListData:
    homeworks: list[HomeworkItem]


@dataclass
class QuestionListData:
    questions: list[QuestionSummary]


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
