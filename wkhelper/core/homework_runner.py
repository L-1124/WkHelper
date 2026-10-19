"""作业提交编排器：事件契约与平台调用。"""

from wkhelper.core.base_platform import BasePlatform
from wkhelper.core.entities import Course, Homework
from wkhelper.core.events import emit_event
from wkhelper.core.results import HomeworkSubmitResult


async def run_homework_submit(
    platform: BasePlatform,
    course: Course,
    homework: Homework,
    answers: dict[str, list[str]],
    concurrency: int = 1,
    delay: float = 0.0,
    delay_jitter: float = 0.0,
    random_order: bool = False,
) -> HomeworkSubmitResult:
    """执行作业答案提交，并派发起止事件。"""
    emit_event("homework.started", {"course_id": str(course.id), "homework_id": str(homework.id), "name": homework.name})
    result = await platform.do_homework(
        homework,
        course,
        answers=answers,
        concurrency=concurrency,
        delay=delay,
        delay_jitter=delay_jitter,
        random_order=random_order,
    )
    emit_event(
        "homework.finished",
        {
            "course_id": str(course.id),
            "homework_id": str(homework.id),
            "total": result.total,
            "submitted_count": result.submitted_count,
            "correct_count": result.correct_count,
        },
    )
    return result
