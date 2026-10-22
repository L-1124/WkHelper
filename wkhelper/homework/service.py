"""homework 业务流程。"""

import logging
import sys
from typing import Any

from wkhelper.domain.actions import NextAction
from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError, AuthError, RiskControlError
from wkhelper.domain.homework import Homework, HomeworkSubmitResult, QuestionSubmitOutcome, QuestionSubmitResult
from wkhelper.homework.submission import generic_submit_homework
from wkhelper.infra.events import EventCallback
from wkhelper.infra.http import AsyncSession
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.parsing import format_leaf_label, iter_leaves_with_context

logger = logging.getLogger(__name__)


async def get_homeworks(platform: BasePlatform, course: Course) -> list[Homework]:
    """获取课程作业列表。

    截止时间直接取自章节树（`score_deadline`/`end_time`）；逐个叶子请求
    leaf_info 既拿不到更准确的值，还会产生 N 次多余往返。
    """
    data = await platform.get_chapter_data(course)
    schedules = await platform.get_leaf_schedules(course)
    homeworks: list[Homework] = []
    for leaf, chapter_name, section_name in iter_leaves_with_context(data):
        # leaf_type 语义以平台前端为准：6=作业；5=考试、7=课件、9=随堂测验、10=公告均不属作业
        if leaf.get("leaf_type") != 6 or not leaf.get("is_show", True):
            continue
        leaf_id = leaf.get("id")
        if leaf_id is None:
            continue
        is_completed = schedules.get(int(leaf_id), 0.0) >= 1.0
        homeworks.append(
            Homework(
                id=str(leaf_id),
                name=format_leaf_label(leaf, chapter_name, section_name),
                deadline=leaf.get("score_deadline") or leaf.get("end_time") or None,
                completed=is_completed,
                metadata={"is_completed": is_completed},
            )
        )
    return homeworks


async def do_homework(
    platform: BasePlatform,
    homework: Homework,
    course: Course,
    answers: dict[str, list[str]] | None = None,
    on_event: EventCallback | None = None,
    concurrency: int = 1,
    delay: float = 0.0,
    delay_jitter: float = 0.0,
    random_order: bool = False,
) -> HomeworkSubmitResult:
    """完成单个作业，提交显式答案集合并返回结构化结果。"""
    questions = await platform.get_leaf_questions(homework.id, course)
    submit_ctx = await platform.prepare_submit_context(homework, course)
    kwargs = platform.get_request_kwargs(course)

    if answers is None:
        raise ValueError("必须显式提供作业答案集合以供提交")

    resolved_pairs: list[tuple[dict[str, Any], list[str]]] = []
    for q in questions:
        pid = str(q.get("problem_id") or q.get("id"))
        if pid in answers:
            resolved_pairs.append((q, answers[pid]))

    async def submit_func(
        problem_id: int,
        answer: list[str],
        course_info: Course,
        client: AsyncSession,
        kwargs: dict[str, Any] | None,
        question: dict[str, Any] | None = None,
    ) -> QuestionSubmitOutcome:
        return await platform.submit_answer(
            homework.name,
            problem_id,
            answer,
            kwargs=kwargs,
            submit_ctx=submit_ctx,
            question=question,
        )

    return await generic_submit_homework(
        resolved_pairs=resolved_pairs,
        submit_func=submit_func,
        course_info=course,
        client=platform.client,
        headers=kwargs,
        on_event=on_event,
        concurrency=concurrency,
        delay=delay,
        delay_jitter=delay_jitter,
        random_order=random_order,
    )


async def submit_question(
    platform: BasePlatform,
    homework: Homework,
    course: Course,
    question_id: str | int,
    answer: list[str] | str,
) -> QuestionSubmitResult:
    """提交单个题目的答案并返回结果。"""
    submit_ctx = await platform.prepare_submit_context(homework, course)
    kwargs = platform.get_request_kwargs(course)
    ans_list = [answer] if isinstance(answer, str) else list(answer)
    try:
        pid = int(question_id)
    except (ValueError, TypeError):
        raise ValueError(f"题目 ID 必须为有效整数: {question_id}") from None

    # 取题目原始结构：填空题必须按题型改用 answers 映射提交
    question: dict[str, Any] | None = None
    try:
        questions = await platform.get_leaf_questions(homework.id, course)
    except (APIError, AuthError) as exc:
        logger.debug(f"拉取题目结构失败，按默认题型提交: {exc}")
        questions = []
    for item in questions:
        if str(item.get("problem_id") or item.get("id")) == str(pid):
            question = item
            break

    try:
        res = await platform.submit_answer(
            homework.name,
            pid,
            ans_list,
            kwargs=kwargs,
            submit_ctx=submit_ctx,
            question=question,
        )
    except RiskControlError as exc:
        web_url = platform.format_web_task_url(course, homework.id, "exercise")
        next_actions = (
            [NextAction(reason="在浏览器打开作业页面完成滑块人机验证后重试", argv=list(sys.argv), description=web_url or "")]
            if web_url
            else None
        )
        raise RiskControlError(str(exc), web_url=web_url, next_actions=next_actions) from exc
    return QuestionSubmitResult(
        question_id=str(question_id),
        submitted_answer=ans_list,
        success=res.success,
        is_correct=res.is_correct,
        correct_answer=res.correct_answer,
        error=res.error,
    )
