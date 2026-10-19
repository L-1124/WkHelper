"""核心调度器：无状态无交互的原子用例执行。"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import msgspec

from wkhelper.core.base_platform import BasePlatform
from wkhelper.core.events import emit_event
from wkhelper.core.models import (
    Course,
    Homework,
    HomeworkSubmitResult,
    VideoParameters,
    VideoPlanSummary,
    VideoStatus,
    VideoTarget,
    VideoTargetResult,
    VideoWatchResult,
    parse_json_text,
)
from wkhelper.core.video import VideoWatchOptions

logger = logging.getLogger(__name__)


class Runner:
    """原子化用例执行器。"""

    def __init__(self, platform: BasePlatform) -> None:
        self.platform = platform

    async def execute_video_task(
        self,
        course: Course,
        video_id: str,
        video_name: str,
        options: VideoWatchOptions | None = None,
    ) -> VideoWatchResult:
        """执行单个视频观看任务。"""
        return await self.platform.do_video(
            video_id=video_id,
            video_name=video_name,
            course=course,
            options=options,
        )

    @staticmethod
    def _failed_result(target: VideoTarget, message: str, status: VideoStatus = "failed") -> VideoTargetResult:
        """构造失败或未执行的结果记录。"""
        return VideoTargetResult(
            course_id=target.course_id,
            video_id=target.video_id,
            video_name=target.name or target.video_id,
            status=status,
            platform_completed=False,
            initial_rate=0.0,
            final_rate=0.0,
            heartbeats_sent=0,
            duration_seconds=0.0,
            message=message,
        )

    @staticmethod
    def _load_completed_records(resume_from: str | Path, target_keys: set[str]) -> list[VideoTargetResult]:
        """从检查点读取已完成记录，仅保留属于当前目标集的条目。"""
        resume_path = Path(resume_from)
        if not resume_path.exists():
            return []

        try:
            checkpoint = parse_json_text(resume_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.debug(f"读取恢复检查点失败: {exc}")
            return []

        if not isinstance(checkpoint, dict):
            return []
        raw_results = checkpoint.get("results")
        if not isinstance(raw_results, list):
            return []

        preserved: list[VideoTargetResult] = []
        seen: set[str] = set()
        for index, raw_item in enumerate(raw_results, 1):
            try:
                record = VideoTargetResult.from_mapping(raw_item, f"检查点第 {index} 条记录")
            except ValueError as exc:
                logger.debug(f"跳过无效检查点记录: {exc}")
                continue
            key = f"{record.course_id}:{record.video_id}"
            if key in target_keys and key not in seen and record.status in ("completed", "threshold_reached"):
                seen.add(key)
                preserved.append(record)
        return preserved

    async def execute_video_plan(
        self,
        courses_map: dict[str, Course],
        targets: list[VideoTarget],
        concurrency: int = 1,
        on_error: str = "stop",
        default_options: VideoWatchOptions | None = None,
        plan_parameters: VideoParameters | None = None,
        cli_parameters: VideoParameters | None = None,
        checkpoint_file: str | Path | None = None,
        resume_from: str | Path | None = None,
    ) -> VideoPlanSummary:
        """执行 Agent 编写的批量视频计划。"""
        base_options = default_options or VideoWatchOptions()
        semaphore = asyncio.Semaphore(max(1, concurrency))

        # 1. 目标排重（相同 course_id + video_id 只保留第一个）
        deduped_targets: list[VideoTarget] = []
        seen_keys: set[str] = set()
        for target in targets:
            key = f"{target.course_id}:{target.video_id}"
            if key not in seen_keys:
                seen_keys.add(key)
                deduped_targets.append(target)

        # 2. 读取已有检查点恢复记录（仅恢复属于本次计划 targets 的完成项）
        preserved_records = self._load_completed_records(resume_from, seen_keys) if resume_from else []
        completed_target_keys = {f"{record.course_id}:{record.video_id}" for record in preserved_records}

        results: list[VideoTargetResult] = list(preserved_records)
        lock = asyncio.Lock()
        stop_flag = False
        plan_deadline = asyncio.get_running_loop().time() + base_options.total_timeout

        def _save_checkpoint() -> None:
            if not checkpoint_file:
                return
            cp_path = Path(checkpoint_file)
            payload: dict[str, Any] = {
                "total_targets": len(deduped_targets),
                "processed": len(results),
                "results": results,
            }
            tmp_path = cp_path.with_suffix(".tmp")
            try:
                cp_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path.write_text(
                    json.dumps(msgspec.to_builtins(payload), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp_path.replace(cp_path)
            except OSError as exc:
                logger.error(f"保存检查点失败: {exc}")

        async def worker(target: VideoTarget) -> None:
            nonlocal stop_flag
            if stop_flag:
                return

            target_key = f"{target.course_id}:{target.video_id}"
            # 已从检查点恢复的完成项直接跳过
            if target_key in completed_target_keys:
                return

            course = courses_map.get(target.course_id)
            if course is None:
                async with lock:
                    results.append(self._failed_result(target, f"找不到课程 ID: {target.course_id}"))
                    _save_checkpoint()
                    if on_error == "stop":
                        stop_flag = True
                return

            async with semaphore:
                if stop_flag:
                    return
                # 总预算耗尽后不再启动新目标
                if asyncio.get_running_loop().time() >= plan_deadline:
                    async with lock:
                        results.append(self._failed_result(target, "整次执行预算已耗尽，目标未启动", status="stopped"))
                        _save_checkpoint()
                        stop_flag = True
                    return
                try:
                    # 内建默认值 < 计划级参数 < 目标级参数 < 本次命令行显式覆盖
                    merged = (
                        base_options.with_parameters(plan_parameters).with_parameters(target.parameters).with_parameters(cli_parameters)
                    )
                    res = await self.platform.do_video(
                        video_id=target.video_id,
                        video_name=target.name or target.video_id,
                        course=course,
                        options=merged,
                    )
                    record = res.with_course_id(target.course_id)
                    async with lock:
                        results.append(record)
                        _save_checkpoint()
                        if record.status in ("failed", "timed_out", "stalled") and on_error == "stop":
                            stop_flag = True

                    # 跨视频切换冷却延迟（带随机抖动）
                    if merged.video_delay > 0 and not stop_flag and len(results) < len(deduped_targets):
                        import random

                        actual_delay = merged.video_delay
                        if merged.jitter > 0:
                            delta = actual_delay * merged.jitter
                            actual_delay = max(0.5, actual_delay + random.uniform(-delta, delta))
                        await asyncio.sleep(actual_delay)
                except Exception as exc:
                    async with lock:
                        results.append(self._failed_result(target, str(exc)))
                        _save_checkpoint()
                        if on_error == "stop":
                            stop_flag = True

        async with asyncio.TaskGroup() as tg:
            for target in deduped_targets:
                tg.create_task(worker(target))

        completed_count = sum(1 for record in results if record.status in ("completed", "threshold_reached"))
        return VideoPlanSummary(
            total=len(deduped_targets),
            completed=completed_count,
            failed=len(results) - completed_count,
            results=results,
        )

    async def execute_homework_submit(
        self,
        course: Course,
        homework: Homework,
        answers: dict[str, list[str]],
        concurrency: int = 1,
        delay: float = 0.0,
        delay_jitter: float = 0.0,
        random_order: bool = False,
    ) -> HomeworkSubmitResult:
        """执行作业答案提交。"""
        emit_event("homework.started", {"course_id": str(course.id), "homework_id": str(homework.id), "name": homework.name})
        result = await self.platform.do_homework(
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
