"""核心程序运行器。"""

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

from wkhelper.core.config import MAX_WORKERS_HOMEWORK, MAX_WORKERS_VIDEO
from wkhelper.core.homework import save_platform_answers
from wkhelper.core.models import Course, Homework
from wkhelper.platform.base import BasePlatform

logger = logging.getLogger(__name__)


class Runner:
    """所有平台的通用命令行运行器。"""

    def __init__(self, platform: BasePlatform):
        self.platform = platform
        self.ui = platform.ui

    @staticmethod
    def _extract_chapter_lecture_tag(text: str) -> tuple[str | None, str] | None:
        """从标题中提取“第X章”和“第Y讲”标签。"""
        chapter_match = re.search(r"(第[一二三四五六七八九十百千万0-9]+章)", text)
        lecture_match = re.search(r"(第[一二三四五六七八九十百千万0-9]+讲)", text)
        if not lecture_match:
            return None
        chapter = chapter_match.group(1) if chapter_match else None
        lecture = lecture_match.group(1)
        return chapter, lecture

    @staticmethod
    def _is_video_homework_lecture_match(video_tag: tuple[str | None, str], homework_tag: tuple[str | None, str]) -> bool:
        """判断视频与作业是否属于同一讲次。"""
        video_chapter, video_lecture = video_tag
        homework_chapter, homework_lecture = homework_tag
        if video_lecture != homework_lecture:
            return False
        if video_chapter and homework_chapter and video_chapter != homework_chapter:
            return False
        return True

    async def _auto_do_homework_by_videos(
        self,
        course: Course,
        selected_video_names: list[str],
    ) -> None:
        """根据已选视频讲次自动完成对应作业。"""
        video_tags = {tag for name in selected_video_names if (tag := self._extract_chapter_lecture_tag(name))}
        if not video_tags:
            logger.info("ℹ️ 未识别到讲次标签，跳过自动作业")
            return

        homeworks = await self.platform.get_homeworks(course)
        if not homeworks:
            logger.info("ℹ️ 无可执行作业，跳过自动作业")
            return

        matched_hws = []
        for hw in homeworks:
            hw_tag = self._extract_chapter_lecture_tag(hw.name)
            if (
                hw_tag
                and any(self._is_video_homework_lecture_match(video_tag, hw_tag) for video_tag in video_tags)
                and not bool(hw.metadata.get("is_completed"))
            ):
                matched_hws.append(hw)

        if not matched_hws:
            logger.info("ℹ️ 未找到对应讲次的未完成作业")
            return

        logger.info(f"🧩 已匹配到 {len(matched_hws)} 份对应作业，自动答题中...")
        tasks: list[tuple[str, Callable[[], Coroutine[Any, Any, None]]]] = []
        for hw in matched_hws:

            def create_task(h=hw, c=course):
                return lambda: self.platform.do_homework(h, c, is_random=False)

            tasks.append((hw.name, create_task()))

        await self._run_parallel_tasks("对应作业任务", tasks, MAX_WORKERS_HOMEWORK)

    async def _run_parallel_tasks(
        self,
        title: str,
        items: list[tuple[str, Callable[[], Coroutine[Any, Any, None]]]],
        max_workers: int,
    ) -> None:
        """并发执行异步任务并输出摘要。"""
        if not items:
            return

        failed: list[tuple[str, str]] = []
        success = 0
        semaphore = asyncio.Semaphore(max_workers)

        async def worker(name: str, task_coro_factory: Callable[[], Coroutine[Any, Any, None]]):
            nonlocal success
            try:
                async with semaphore:
                    await task_coro_factory()
                success += 1
            except Exception as exc:  # noqa: BLE001
                failed.append((name, str(exc)))
            finally:
                tracker.update()

        with self.ui.track_progress(title, len(items)) as tracker:
            async with asyncio.TaskGroup() as tg:
                for name, fn in items:
                    tg.create_task(worker(name, fn))

        msg = f"📊 {title}摘要：总数 {len(items)}，成功 {success}，失败 {len(failed)}"
        if not failed:
            logger.info(msg)
        else:
            logger.warning(msg)

        if failed:
            self.ui.show_table(
                f"❌ {title}失败项目",
                ["任务名", "错误原因"],
                [[name, str(exc)] for name, exc in failed],
            )

    async def run_main_menu(self):
        """顶级功能菜单。"""
        logger.info(f"👤 登录成功：{self.platform.user.name if self.platform.user else '未知'}")

        logger.info("📚 正在获取课程列表...")
        courses = await self.platform.get_courses()

        if not courses:
            logger.warning("⚠️ 未找到任何课程")
            return

        while True:
            mode = await self.ui.select_one(
                f"功能菜单 (共 {len(courses)} 门课程)",
                ["学习课程视频", "完成课程作业", "下载课程答案", "退出"],
            )
            if mode in (None, "退出"):
                break

            # 课程选择
            course_names = [c.name for c in courses]
            selected_names = await self.ui.select_many("请选择要操作的课程", course_names)
            if not selected_names:
                continue

            target_courses = [c for c in courses if c.name in selected_names]

            if mode == "学习课程视频":
                await self.batch_learn_videos(target_courses)
            elif mode == "完成课程作业":
                await self.batch_do_homework(target_courses)
            elif mode == "下载课程答案":
                await self.batch_save_answers(target_courses)

            logger.info("✅ 流程结束！\n")

    async def batch_learn_videos(self, target_courses: list[Course]):
        """学习所选课程的视频。"""
        for idx, course in enumerate(target_courses, 1):
            logger.info(f"\n🎯 [{idx}/{len(target_courses)}] 处理课程: {course.name}")
            videos_dict = await self.platform.get_videos(course)
            if not videos_dict:
                logger.warning("暂无视频")
                continue

            video_list = list(videos_dict.items())
            video_names = [vname for _, vname in video_list]
            completed_video_ids = set(course.metadata.get("completed_video_ids", set()))
            disabled_video_names = {vname for vid, vname in video_list if int(vid) in completed_video_ids}
            selected_vnames = await self.ui.select_many(f"选择视频 - {course.name}", video_names, disabled_choices=disabled_video_names)

            if not selected_vnames:
                continue

            auto_do_homework = await self.ui.confirm(
                "完成视频后是否自动完成对应讲次作业？",
                default=False,
            )

            # 构造任务
            tasks: list[tuple[str, Callable[[], Coroutine[Any, Any, None]]]] = []
            for vid, vname in video_list:
                if vname in selected_vnames:
                    # 使用闭包绑定变量
                    def create_task(v_id=vid, v_name=vname, c=course):
                        return lambda: self.platform.do_video(str(v_id), v_name, c)

                    tasks.append((vname, create_task()))

            await self._run_parallel_tasks("视频任务", tasks, MAX_WORKERS_VIDEO)
            if auto_do_homework:
                await self._auto_do_homework_by_videos(course, selected_vnames)

    async def batch_do_homework(self, target_courses: list[Course]):
        """完成所选课程作业。"""
        for idx, course in enumerate(target_courses, 1):
            logger.info(f"\n📝 [{idx}/{len(target_courses)}] 获取课程作业: {course.name}")
            homeworks = await self.platform.get_homeworks(course)

            if not homeworks:
                logger.warning("暂无作业")
                continue

            hw_labels = []
            hw_map: dict[str, Homework] = {}
            for hw in homeworks:
                deadline_str = datetime.fromtimestamp(hw.deadline / 1000).strftime("%Y-%m-%d %H:%M") if hw.deadline else "无截止时间"
                label = f"{hw.name} (截止: {deadline_str})"
                hw_labels.append(label)
                hw_map[label] = hw

            selected_labels = await self.ui.select_many(
                f"选择作业 - {course.name}",
                hw_labels,
                disabled_choices={label for label, hw in hw_map.items() if bool(hw.metadata.get("is_completed"))},
            )
            if not selected_labels:
                continue

            target_hws = [hw_map[label] for label in selected_labels]
            # 课堂作业统一使用自动答题；随机答题仅用于探测答案，不用于课堂作业提交流程。
            is_random = False
            logger.info(f"🧠 模式：自动，共 {len(target_hws)} 份作业")

            tasks: list[tuple[str, Callable[[], Coroutine[Any, Any, None]]]] = []
            for hw in target_hws:
                # 使用闭包绑定变量
                def create_task(h=hw, c=course, r=is_random):
                    return lambda: self.platform.do_homework(h, c, is_random=r)

                tasks.append((hw.name, create_task()))

            await self._run_parallel_tasks("作业任务", tasks, MAX_WORKERS_HOMEWORK)

    async def batch_save_answers(self, target_courses: list[Course]):
        """保存所选课程答案。"""
        do_random_answer_first = await self.ui.confirm(
            "下载答案前是否先进行随机答题以获取正确答案？",
            default=False,
        )

        for course in target_courses:
            try:
                if do_random_answer_first:
                    logger.info(f"🎲 先执行随机答题以补充答案：{course.name}")
                    homeworks = await self.platform.get_homeworks(course)
                    for hw in homeworks:
                        if bool(hw.metadata.get("is_completed")):
                            continue
                        await self.platform.do_homework(hw, course, is_random=True)

                # 注意：此处要求 save_platform_answers 也是异步的
                await save_platform_answers(self.platform, course)
                logger.info(f"✅ 下载答案完成：{course.name}")
            except Exception as exc:  # noqa: BLE001
                logger.error(f"❌ 下载答案失败：{course.name}，原因：{exc}")
