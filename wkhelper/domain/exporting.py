"""导出结果与结构化题目导出模型。"""

from typing import Any

from wkhelper.domain.homework import OptionItem
from wkhelper.domain.model import Model


class OutputFileResult(Model):
    output_file: str
    written: bool


class ExportResult(Model):
    file_path: str | None
    homeworks_count: int
    questions_count: int
    resources_count: int = 0


class ExportedQuestionItem(Model):
    """单道导出题目数据结构。"""

    index: int
    id: str
    type: str | None = None
    type_id: int | None = None
    prompt: str
    options: list[OptionItem] = []
    correct_answer: list[str] | str | dict[str, Any] | None = None
    user_answer: list[str] | str | dict[str, Any] | None = None
    is_correct: bool | None = None
    score: float | str | None = None
    library_id: int | str | None = None
    resources: list[str] = []


class ExportedHomeworkItem(Model):
    """按作业分组的题目集合。"""

    id: str
    name: str
    deadline: int | str | None = None
    completed: bool = False
    total_questions: int = 0
    questions: list[ExportedQuestionItem] = []


class CourseQuestionsExport(Model):
    """课程全量作业题目导出模型。"""

    course_id: str
    course_name: str
    platform_id: str | None = None
    total_homeworks: int
    total_questions: int
    homeworks: list[ExportedHomeworkItem] = []
