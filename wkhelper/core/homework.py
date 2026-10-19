"""通用作业处理逻辑与结构化提交。"""

import asyncio
import logging
from typing import Any, Protocol

from wkhelper.core.entities import Course
from wkhelper.core.events import EventCallback, notifier
from wkhelper.core.results import HomeworkSubmitResult, QuestionSubmitOutcome, SubmitItem
from wkhelper.core.transport import AsyncSession

logger = logging.getLogger(__name__)

# 平台原始题目结构（动态载荷，字段由平台 API 决定）
type QuestionPayload = dict[str, Any]
type HeaderMap = dict[str, Any]

_FILL_IN_TYPE = 4
_JUDGEMENT_TYPE = 6

# 判断题前端只认 true/false；兼容常见的字母与中文写法
_TRUE_ANSWERS = {"A", "T", "TRUE", "1", "对", "正确", "√"}
_FALSE_ANSWERS = {"B", "F", "FALSE", "0", "错", "错误", "×"}


def normalize_judgement_answer(question: QuestionPayload | None, answer: list[str]) -> list[str]:
    """把判断题答案归一为平台约定的 `true` / `false`。"""
    if question_type(question) != _JUDGEMENT_TYPE:
        return answer
    normalized: list[str] = []
    for item in answer:
        token = item.strip().upper()
        if token in _TRUE_ANSWERS:
            normalized.append("true")
        elif token in _FALSE_ANSWERS:
            normalized.append("false")
        else:
            normalized.append(item)
    return normalized


def question_type(question: QuestionPayload | None) -> int | None:
    """题目题型：优先取 `content.ProblemType`（两平台实际字段），退回 `type`。

    题型取值：1/6 单选判断、2 多选、3 投票、4 填空、5 主观题。
    """
    if not isinstance(question, dict):
        return None
    content = question.get("content")
    raw: Any = None
    if isinstance(content, dict):
        raw = content.get("ProblemType")
        if raw is None:
            raw = content.get("Type")
    if raw is None:
        raw = question.get("type")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_fill_in_question(question: QuestionPayload | None) -> bool:
    """是否为填空题型：必须走 `answers` 映射而不是 `answer` 列表。"""
    return question_type(question) == _FILL_IN_TYPE


class SubmitFunc(Protocol):
    """单题提交函数协议。"""

    async def __call__(
        self,
        problem_id: int,
        answer: list[str],
        course_info: Course,
        client: AsyncSession,
        kwargs: HeaderMap | None,
        question: QuestionPayload | None = None,
    ) -> QuestionSubmitOutcome: ...


async def generic_submit_homework(
    resolved_pairs: list[tuple[QuestionPayload, list[str]]],
    submit_func: SubmitFunc,
    course_info: Course,
    client: AsyncSession,
    headers: HeaderMap | None = None,
    on_event: EventCallback | None = None,
    concurrency: int = 1,
    delay: float = 0.0,
    delay_jitter: float = 0.0,
    random_order: bool = False,
) -> HomeworkSubmitResult:
    """异步并发提交题目答案，返回结构化逐题记录与汇总。"""

    _notify = notifier(on_event)

    if not resolved_pairs:
        return HomeworkSubmitResult(total=0, submitted_count=0, correct_count=0, items=[])

    success_count = 0
    correct_count = 0
    processed_count = 0
    items: list[SubmitItem] = []

    semaphore = asyncio.Semaphore(max(1, concurrency))
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
            user_info = q.get("user") or {}
            my_count = user_info.get("my_count") or 0
            # 单题可作答次数在 user.count（0=不限次）；顶部 max_retry 属于整卷字段
            retry_limit = int(user_info.get("count") or 0)
            already_submitted = bool(user_info.get("answer") or user_info.get("my_answer") or my_count > 0)
            already_correct = bool(user_info.get("is_right") or user_info.get("is_correct"))

            if already_submitted and already_correct:
                item_record.submitted = True
                item_record.is_correct = True
                async with lock:
                    success_count += 1
                    correct_count += 1
                return

            if already_submitted and retry_limit > 0 and my_count >= retry_limit:
                item_record.submitted = True
                item_record.is_correct = already_correct
                item_record.error = "作答次数已用尽"
                if already_correct:
                    async with lock:
                        correct_count += 1
                async with lock:
                    success_count += 1
                return
            if delay > 0 and not already_submitted:
                import random

                actual_delay = max(0.05, delay + random.uniform(-delay_jitter, delay_jitter) if delay_jitter > 0 else delay)
                await asyncio.sleep(actual_delay)

            async with semaphore:
                result = await submit_func(pid_int, answer, course_info, client, headers, q)

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
            elif result.error_code == 80001 and already_submitted:
                item_record.submitted = True
                item_record.is_correct = already_correct
                if already_correct:
                    async with lock:
                        correct_count += 1
                async with lock:
                    success_count += 1
            else:
                item_record.error = result.error or "平台返回提交失败"
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

    pairs_to_process = list(enumerate(resolved_pairs, 1))
    if random_order:
        import random

        random.shuffle(pairs_to_process)

    async with asyncio.TaskGroup() as tg:
        for i, (q, answer) in pairs_to_process:
            tg.create_task(worker(i, q, answer))

    items.sort(key=lambda x: x.index)

    return HomeworkSubmitResult(
        total=len(resolved_pairs),
        submitted_count=success_count,
        correct_count=correct_count,
        items=items,
    )
