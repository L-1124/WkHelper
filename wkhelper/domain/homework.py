"""作业与题目数据。"""

from typing import Any

from pydantic import Field

from wkhelper.domain.model import Model


class Homework(Model):
    """作业模型。"""

    id: int | str
    name: str
    deadline: str | int | None = None
    completed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)  # 平台动态载荷


class Question(Model):
    """题目模型。"""

    id: int | str
    content: str
    options: list[str] = Field(default_factory=list)
    answer: list[str] | str | None = None
    library_id: str | None = None
    version: str | None = None


class HomeworkItem(Model):
    id: str
    name: str
    deadline: str | int | None = None
    completed: bool = False


class OptionItem(Model):
    key: str
    text: str


class QuestionSummary(Model):
    index: int
    id: str
    type: str | None
    prompt: str
    content_truncated: bool
    options: list[OptionItem]
    submitted: bool
    is_correct: bool | None


class QuestionDetail(Model):
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
    resources: list[str] = Field(default_factory=list)


class QuestionOutline(Model):
    index: int
    id: str
    type: str | None
    library_id: int | str | None


class HomeworkOutline(Model):
    course_id: str
    homework_id: str
    total_questions: int
    questions_summary: list[QuestionOutline]


class HomeworkListData(Model):
    homeworks: list[HomeworkItem]


class QuestionListData(Model):
    questions: list[QuestionSummary]


class SubmitItem(Model):
    index: int
    question_id: str
    submitted: bool
    is_correct: bool
    correct_answer: list[str] | None = None
    error: str | None = None


class HomeworkSubmitResult(Model):
    total: int
    submitted_count: int
    correct_count: int
    items: list[SubmitItem]


class QuestionSubmitOutcome(Model):
    """平台单题提交回执。"""

    success: bool
    is_correct: bool
    correct_answer: list[str]
    error: str | None = None
    error_code: int | None = None


class QuestionSubmitResult(Model):
    question_id: str
    submitted_answer: list[str]
    success: bool
    is_correct: bool
    correct_answer: list[str]
    error: str | None = None


class HomeworkSubmitPreview(Model):
    dry_run: bool
    course_id: str
    homework_id: str
    answers_count: int
    validated: bool


class QuestionSubmitPreview(Model):
    dry_run: bool
    course_id: str
    homework_id: str
    question_id: str
    answer: list[str]
