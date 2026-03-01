"""通用作业处理逻辑分析与提交。"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from wkhelper.core.config import MAX_WORKERS_DOWNLOAD, MAX_WORKERS_HOMEWORK
from wkhelper.core.db import db
from wkhelper.core.utils import get_random_sleep

logger = logging.getLogger(__name__)


class SubmitFunc(Protocol):
    """提交函数协议。"""

    async def __call__(
        self,
        problem_id: int,
        answer: list[str],
        course_info: Any,
        client: httpx.AsyncClient,
        kwargs: dict[str, Any] | None,
    ) -> dict[str, Any]: ...


def extract_answers(questions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """从题目列表中提取答案。"""
    hw_answers = {}
    for q in questions:
        # 提取 LibraryID
        library_id = None
        if "content" in q:
            library_id = q["content"].get("LibraryID") or q["content"].get("library_id")

        version = q["content"].get("Version")

        if not library_id or not version:
            continue

        ans = None
        if "user" in q and q["user"].get("answer"):
            ans = q["user"]["answer"]

        if library_id and ans:
            lib_id_str = str(library_id)
            if lib_id_str not in hw_answers:
                hw_answers[lib_id_str] = {}
            hw_answers[lib_id_str][version] = ans

    return hw_answers


async def save_platform_answers(platform: Any, course: Any):
    """通用扫描并保存课程答案逻辑（异步并发）。"""
    logger.info(f"🔍 正在扫描课程答案: {course.name}")
    homeworks = await platform.get_homeworks(course)

    if not homeworks:
        logger.warning("⚠️ 该课程暂无作业")
        return

    count = 0
    semaphore = asyncio.Semaphore(MAX_WORKERS_DOWNLOAD)

    async def _fetch_and_save(hw):
        nonlocal count
        try:
            async with semaphore:
                questions = await platform.get_leaf_questions(hw.id, course)
            answers = extract_answers(questions)
            inner_count = 0
            for lib_id, versions in answers.items():
                for ver, ans in versions.items():
                    db.save_answer(lib_id, ver, ans)
                    inner_count += 1
            count += inner_count
        except Exception as e:
            logger.error(f"  ❌ 获取作业 {hw.name} 答案失败: {e}")

    async with asyncio.TaskGroup() as tg:
        for hw in homeworks:
            tg.create_task(_fetch_and_save(hw))

    if count == 0:
        logger.warning("⚠️ 未找到任何可保存的答案")
    else:
        logger.info(f"✅ 已保存 {count} 条答案到数据库")


async def process_question(
    idx: int,
    q: dict[str, Any],
    chapter_id: int,
    leaf_type_id: int,
    course_info: Any,
    client: httpx.AsyncClient,
    submit_func: SubmitFunc,
    headers: dict[str, Any] | None = None,
) -> tuple[bool, bool]:
    """处理单个题目：查找答案 -> 提交"""

    # 1. 提取 LibID/Version
    library_id = None
    version = None
    if "content" in q:
        library_id = q["content"].get("LibraryID") or q["content"].get("library_id")
        version = q["content"].get("Version")

    if not library_id or not version:
        logger.warning(f"  ⚠️ 第{idx}题 无法获取 LibraryID 或 Version，跳过")
        return False, False

    library_id = str(library_id)

    # 2. 查找数据库
    answer = db.get_answer(library_id, version)
    if not answer:
        logger.debug(f"  ⏭️ 第{idx}题 无答案 (LibID: {library_id}, Ver: {version})，跳过")
        return False, False

    # 3. 验证
    problem_id = q.get("problem_id") or q.get("id")
    if problem_id is None:
        logger.warning(f"  ⚠️ 第{idx}题 无法获取题目ID，跳过")
        return False, False

    if q.get("user", {}).get("my_count", 0) >= q.get("max_retry", 1):
        logger.debug(f"  ⏭️ 第{idx}题 达到最大回答次数，跳过")
        return False, False

    # 4. 提交
    final_answer: list[str] = answer
    result = await submit_func(int(problem_id), final_answer, course_info, client, headers)

    if result.get("success"):
        if result.get("is_correct"):
            logger.debug(f"  ✅ 第{idx}题 提交成功 - 回答正确")
            return True, True
        else:
            correct_ans = ", ".join(result.get("correct_answer", []))
            logger.warning(f"  ⚠️ 第{idx}题 提交成功 - 回答错误，正确答案: {correct_ans}")
            return True, False
    else:
        logger.error(f"  ❌ 第{idx}题 提交失败")
        return False, False


async def generic_process_homework(
    questions: list[Any],
    submit_func: SubmitFunc,
    course_info: Any,
    client: httpx.AsyncClient,
    chapter_id: int = 0,
    leaf_type_id: int = 0,
    headers: dict[str, Any] | None = None,
    on_progress: Callable[[int, int], None | Awaitable[None]] | None = None,
):
    """异步并发处理作业题目列表"""

    async def _maybe_call(result: None | Awaitable[None]) -> None:
        if result is not None:
            await result

    if not questions:
        logger.warning("  ⚠️ 未获取到题目")
        return

    logger.debug(f"  📋 共 {len(questions)} 道题目")

    success_count = 0
    correct_count = 0
    processed_count = 0
    semaphore = asyncio.Semaphore(MAX_WORKERS_HOMEWORK)
    progress_lock = asyncio.Lock()

    async def worker(idx: int, q: dict[str, Any]):
        nonlocal success_count, correct_count, processed_count
        try:
            async with semaphore:
                s, c = await process_question(
                    idx,
                    q,
                    chapter_id,
                    leaf_type_id,
                    course_info,
                    client,
                    submit_func,
                    headers,
                )
            if s:
                success_count += 1
            if c:
                correct_count += 1
        except Exception as e:
            logger.error(f"  ❌ 处理题目 {idx} 失败: {e}")
        finally:
            async with progress_lock:
                processed_count += 1
                if on_progress:
                    await _maybe_call(on_progress(processed_count, len(questions)))

    async with asyncio.TaskGroup() as tg:
        for i, q in enumerate(questions, 1):
            tg.create_task(worker(i, q))

    logger.debug(f"  📊 提交 {success_count}/{len(questions)} 道，正确 {correct_count}/{success_count} 道")


async def generic_random_answer(
    questions: list[Any],
    submit_func: SubmitFunc,
    course_info: Any,
    client: httpx.AsyncClient,
    headers: dict[str, Any] | None = None,
    on_progress: Callable[[int, int], None | Awaitable[None]] | None = None,
):
    """处理题目的随机答题（由于随机答题通常需要模拟人的行为，此处按序执行并带随机等待）"""

    async def _maybe_call(result: None | Awaitable[None]) -> None:
        if result is not None:
            await result

    if not questions:
        logger.warning("  ⚠️ 未获取到题目")
        return

    logger.debug(f"  📋 共 {len(questions)} 道题目")

    processed_count = 0
    for i, q in enumerate(questions, 1):
        if q.get("user", {}).get("is_right", False):
            logger.debug(f"  ✅ 第{i}题 已正确，跳过")
            processed_count += 1
            if on_progress:
                await _maybe_call(on_progress(processed_count, len(questions)))
            continue

        if q.get("user", {}).get("my_count", 0) >= q.get("max_retry", 999):
            logger.debug(f"  ⏭️ 第{i}题 次数耗尽，跳过")
            processed_count += 1
            if on_progress:
                await _maybe_call(on_progress(processed_count, len(questions)))
            continue

        problem_id = q.get("problem_id") or q.get("id")

        # 获取选项
        options = []
        if "content" in q and "Options" in q["content"]:
            options = [opt["key"] for opt in q["content"]["Options"]]

        if not options:
            options = ["A", "B", "C", "D"]

        answer = [random.choice(options)]

        result = await submit_func(problem_id, answer, course_info, client, headers)

        if result.get("success"):
            status = "正确" if result.get("is_correct") else "错误"
            correct_ans = result.get("correct_answer")
            logger.debug(f"  🎲 第{i}题 随机提交 {answer} -> {status}")
            if correct_ans:
                logger.debug(f"     正确答案: {correct_ans}")
                library_id = q["content"].get("LibraryID") or q["content"].get("library_id")
                version = q["content"].get("Version")
                if library_id and version:
                    db.save_answer(str(library_id), str(version), correct_ans)
        else:
            logger.error(f"  ❌ 第{i}题 提交失败")

        processed_count += 1
        if on_progress:
            await _maybe_call(on_progress(processed_count, len(questions)))
        await asyncio.sleep(get_random_sleep(2, 3))
