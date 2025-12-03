from typing import Any, Required, TypedDict


class Course(TypedDict):
    name: Required[str]
    classroom_id: Required[int]
    university_id: Required[int]
    id: Required[int]


class Homework(TypedDict):
    id: Required[int]
    name: Required[str]
    start_time: Required[int | None]
    score_deadline: Required[int | None]
    is_score: Required[bool | None]
    chapter_id: Required[int | None]


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
