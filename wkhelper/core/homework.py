"""通用作业处理逻辑与结构化提交。"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Protocol

import niquests

from wkhelper.cli.events import emit_event
from wkhelper.core.config import MAX_WORKERS_HOMEWORK
from wkhelper.core.models import Course, HomeworkSubmitResult, JsonValue, QuestionSubmitOutcome, SubmitItem

logger = logging.getLogger(__name__)

# 平台原始题目结构（动态载荷，字段由平台 API 决定）
type QuestionPayload = dict[str, Any]
type HeaderMap = dict[str, Any]
type EventCallback = Callable[[str, dict[str, JsonValue]], None]


class SubmitFunc(Protocol):
    """单题提交函数协议。"""

    async def __call__(
        self,
        problem_id: int,
        answer: list[str],
        course_info: Course,
        client: niquests.AsyncSession,
        kwargs: HeaderMap | None,
    ) -> QuestionSubmitOutcome: ...


async def generic_submit_homework(
    resolved_pairs: list[tuple[QuestionPayload, list[str]]],
    submit_func: SubmitFunc,
    course_info: Course,
    client: niquests.AsyncSession,
    headers: HeaderMap | None = None,
    on_event: EventCallback | None = None,
) -> HomeworkSubmitResult:
    """异步并发提交题目答案，返回结构化逐题记录与汇总。"""

    def _notify(event: str, payload: dict[str, JsonValue]) -> None:
        if on_event:
            on_event(event, payload)
        else:
            emit_event(event, payload)

    if not resolved_pairs:
        return HomeworkSubmitResult(total=0, submitted_count=0, correct_count=0, items=[])

    success_count = 0
    correct_count = 0
    processed_count = 0
    items: list[SubmitItem] = []

    semaphore = asyncio.Semaphore(MAX_WORKERS_HOMEWORK)
    lock = asyncio.Lock()

    async def worker(idx: int, q: QuestionPayload, answer: list[str]) -> None:
        nonlocal success_count, correct_count, processed_count
        problem_id = q.get("problem_id") or q.get("id")
        pid_str = str(problem_id) if problem_id is not None else ""

        item_record = SubmitItem(index=idx, question_id=pid_str, submitted=False, is_correct=False)

        try:
            if problem_id is None:
                item_record.error = "缺少有效题目ID"
                return

            try:
                pid_int = int(problem_id)
            except (ValueError, TypeError):
                item_record.error = f"题目ID格式无效: {problem_id}"
                return

            async with semaphore:
                result = await submit_func(pid_int, answer, course_info, client, headers)

            if result.success:
                item_record.submitted = True
                async with lock:
                    success_count += 1

                if result.is_correct:
                    item_record.is_correct = True
                    async with lock:
                        correct_count += 1
                else:
                    item_record.correct_answer = result.correct_answer
            else:
                item_record.error = "平台返回提交失败"

        except Exception as e:
            item_record.error = str(e)
        finally:
            async with lock:
                processed_count += 1
                items.append(item_record)
                _notify(
                    "homework.progress",
                    {
                        "processed": processed_count,
                        "total": len(resolved_pairs),
                        "success": success_count,
                    },
                )

    async with asyncio.TaskGroup() as tg:
        for i, (q, answer) in enumerate(resolved_pairs, 1):
            tg.create_task(worker(i, q, answer))

    items.sort(key=lambda x: x.index)

    return HomeworkSubmitResult(
        total=len(resolved_pairs),
        submitted_count=success_count,
        correct_count=correct_count,
        items=items,
    )
