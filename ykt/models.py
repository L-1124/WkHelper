from typing import Any, Required, TypedDict


class Course(TypedDict):
    name: str
    classroom_id: int
    university_id: int
    id: int


class Homework(TypedDict):
    id: int
    name: str
    start_time: int | None
    score_deadline: int | None
    is_score: bool | None
    chapter_id: int | None


class SubmitResult(TypedDict):
    success: bool
    is_correct: bool
    correct_answer: list[str]


class UserInfo(TypedDict):
    id: int
    name: str
    school: str


class ClassroomInfo(TypedDict):
    id: int
    course_id: int
    course_sign: str
    free_sku_id: int


class Question(TypedDict, total=False):
    id: Required[int]
    index: Required[int]
    max_retry: Required[int]
    problem_id: Required[int | None]
    user: Required[dict[str, Any]]
    content: Required[dict[str, Any]]
