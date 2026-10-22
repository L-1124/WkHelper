"""课程数据。"""

from typing import Any

from pydantic import Field, JsonValue

from wkhelper.domain.model import Model


class Course(Model):
    """课程模型。"""

    id: int | str
    name: str
    platform_id: str  # 'ykt' 或 'xtzx'
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)  # 平台特定数据 (classroom_id, sku_id 等)，动态载荷


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


class CourseItem(Model):
    id: str
    name: str
    platform: str
    classroom_id: int | str | None = None


class ModuleEvaluation(Model):
    """课程单个考核模块统计（视频、作业、讨论、考试等）。"""

    name: str
    weight: float | None = None
    total_count: int = 0
    completed_count: int = 0
    rate: float = 0.0
    score: float | None = None


class ExamScoreItem(Model):
    """考场考试得分与作答明细。"""

    exam_id: str
    title: str
    status: str
    user_score: float | None = None
    total_score: float | None = None
    problem_count: int = 0
    done_count: int = 0


class CourseScoreSummary(Model):
    """整门课程考核大纲与成绩看板。"""

    modules: list[ModuleEvaluation] = Field(default_factory=list)
    exams: list[ExamScoreItem] = Field(default_factory=list)
    total_predicted_score: float | None = None


class CourseDetail(Model):
    id: str
    name: str
    platform: str
    metadata: dict[str, JsonValue]
    scores: CourseScoreSummary | None = None


class CourseListData(Model):
    courses: list[CourseItem]
