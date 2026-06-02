"""通用作业处理逻辑分析与提交。"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import niquests

from wkhelper.core.config import MAX_WORKERS_DOWNLOAD, MAX_WORKERS_HOMEWORK
from wkhelper.core.db import db
from wkhelper.core.utils import get_random_sleep

logger = logging.getLogger(__name__)

type ProgressCallback = Callable[[int, int], None | Awaitable[None]]
type QuestionPayload = dict[str, Any]
type HeaderMap = dict[str, Any]
type AnswerStore = dict[str, dict[str, Any]]


class SubmitFunc(Protocol):
    """提交函数协议。"""

    async def __call__(
        self,
        problem_id: int,
        answer: list[str],
        course_info: Any,
        client: niquests.AsyncSession,
        kwargs: HeaderMap | None,
    ) -> QuestionPayload: ...


def extract_answers(questions: list[QuestionPayload]) -> AnswerStore:
    """从题目列表中提取答案。"""
    hw_answers = {}
    for idx, q in enumerate(questions, 1):
        # 提取 LibraryID 和 Version
        content = q.get("content", {})
        library_id = content.get("LibraryID") or content.get("library_id")
        version = content.get("Version")

        if not library_id or not version:
            logger.debug(f"  ⏭️ 题目 {idx} 缺少 LibraryID 或 Version，跳过")
            continue

        ans = None
        user_info = q.get("user", {})
        if user_info.get("answer"):
            ans = user_info["answer"]

        if not ans:
            logger.debug(f"  ⏭️ 题目 {idx} (LibID: {library_id}) 无答案，跳过")
            continue

        lib_id_str = str(library_id)
        if lib_id_str not in hw_answers:
            hw_answers[lib_id_str] = {}
        hw_answers[lib_id_str][version] = ans
        logger.debug(f"  ✅ 题目 {idx} 已提取答案 (LibID: {library_id}, Ans: {ans})")

    return hw_answers


async def save_platform_answers(platform: Any, course: Any):
    """通用扫描并保存课程答案逻辑（异步并发）。"""
    logger.info(f"🔍 正在扫描课程答案: {course.name}")
    homeworks = await platform.get_homeworks(course)

    if not homeworks:
        logger.warning("⚠️ 该课程暂无作业")
        return

    logger.debug(f"📋 找到 {len(homeworks)} 个作业")

    count = 0
    semaphore = asyncio.Semaphore(MAX_WORKERS_DOWNLOAD)

    async def _fetch_and_save(hw):
        nonlocal count
        try:
            async with semaphore:
                questions = await platform.get_leaf_questions(hw.id, course)
            logger.debug(f"📝 作业 '{hw.name}' 获取了 {len(questions)} 道题目")
            answers = extract_answers(questions)
            inner_count = 0
            for lib_id, versions in answers.items():
                for ver, ans in versions.items():
                    db.save_answer(lib_id, ver, ans)
                    inner_count += 1
            if inner_count > 0:
                logger.info(f"  ✅ 作业 '{hw.name}' 已保存 {inner_count} 条答案")
            count += inner_count
        except Exception as e:
            logger.error(f"  ❌ 获取作业 {hw.name} 答案失败: {e}")

    async with asyncio.TaskGroup() as tg:
        for hw in homeworks:
            tg.create_task(_fetch_and_save(hw))

    if count == 0:
        logger.warning("⚠️ 未找到任何可保存的答案")
    else:
        logger.info(f"✅ 下载答案完成：{course.name}")
        logger.info(f"💾 共保存 {count} 条答案到数据库")


async def process_question(
    idx: int,
    q: QuestionPayload,
    chapter_id: int,
    leaf_type_id: int,
    course_info: Any,
    client: niquests.AsyncSession,
    submit_func: SubmitFunc,
    headers: HeaderMap | None = None,
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
    client: niquests.AsyncSession,
    chapter_id: int = 0,
    leaf_type_id: int = 0,
    headers: HeaderMap | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
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

    async def worker(idx: int, q: QuestionPayload):
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
    questions: list[QuestionPayload],
    submit_func: SubmitFunc,
    course_info: Any,
    client: niquests.AsyncSession,
    headers: HeaderMap | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """处理题目的随机答题，异步并发执行。"""

    async def _maybe_call(result: None | Awaitable[None]) -> None:
        if result is not None:
            await result

    if not questions:
        logger.warning("  ⚠️ 未获取到题目")
        return

    logger.debug(f"  📋 共 {len(questions)} 道题目")

    processed_count = 0
    semaphore = asyncio.Semaphore(MAX_WORKERS_HOMEWORK)
    progress_lock = asyncio.Lock()

    async def worker(idx: int, q: dict[str, Any]):
        nonlocal processed_count
        try:
            if q.get("user", {}).get("is_right", False):
                logger.debug(f"  ✅ 第{idx}题 已正确，跳过")
                return
            if q.get("user", {}).get("my_count", 0) >= q.get("max_retry", 999):
                logger.debug(f"  ⏭️ 第{idx}题 次数耗尽，跳过")
                return

            problem_id_raw = q.get("problem_id") or q.get("id")
            if problem_id_raw is None:
                logger.error(f"  ❌ 第{idx}题 缺少有效题目 ID，跳过")
                return
            try:
                problem_id = int(problem_id_raw)
            except (TypeError, ValueError):
                logger.error(f"  ❌ 第{idx}题 缺少有效题目 ID，跳过")
                return

            options = []
            if "content" in q and "Options" in q["content"]:
                options = [opt["key"] for opt in q["content"]["Options"]]
            if not options:
                options = ["A", "B", "C", "D"]

            answer = [random.choice(options)]

            async with semaphore:
                result = await submit_func(problem_id, answer, course_info, client, headers)
                await asyncio.sleep(get_random_sleep(1, 2))

            if result.get("success"):
                status = "正确" if result.get("is_correct") else "错误"
                correct_ans = result.get("correct_answer")
                logger.debug(f"  🎲 第{idx}题 随机提交 {answer} -> {status}")
                if correct_ans:
                    logger.debug(f"     正确答案: {correct_ans}")
                    library_id = q["content"].get("LibraryID") or q["content"].get("library_id")
                    version = q["content"].get("Version")
                    if library_id and version:
                        db.save_answer(str(library_id), str(version), correct_ans)
            else:
                logger.error(f"  ❌ 第{idx}题 提交失败")
        finally:
            async with progress_lock:
                processed_count += 1
                if on_progress:
                    await _maybe_call(on_progress(processed_count, len(questions)))

    async with asyncio.TaskGroup() as tg:
        for i, q in enumerate(questions, 1):
            tg.create_task(worker(i, q))

    logger.debug(f"  📊 随机答题完成，共处理 {processed_count}/{len(questions)} 道")
