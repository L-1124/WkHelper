from typing import Any, Required, TypedDict


class Course(TypedDict):
    name: str
    classroom_id: int
    sign: str
    product_id: int
    sku_id: int


class Homework(TypedDict):
    id: Required[int]
    name: Required[str]
    start_time: Required[int]
    score_deadline: Required[int]
    is_score: Required[bool]
    chapter_id: Required[int]


class SubmitResult(TypedDict):
    success: Required[bool]
    is_correct: Required[bool]
    correct_answer: Required[list[str]]


class UserInfo(TypedDict):
    id: Required[int]
    name: Required[str]
    school: Required[str]


class ClassroomInfo(TypedDict):
    id: Required[int]
    course_id: Required[int]
    course_sign: Required[str]
    free_sku_id: Required[int]


class Question(TypedDict, total=False):
    id: Required[int]
    index: Required[int]
    max_retry: Required[int]
    problem_id: Required[int | None]
    user: Required[dict[str, Any]]
    content: Required[dict[str, Any]]
