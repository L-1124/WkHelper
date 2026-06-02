"""平台基础接口。"""

import asyncio
import logging
import math
import re
from abc import ABC, abstractmethod
from typing import Any

import niquests

from wkhelper.core.models import Course, Homework, UserInfo
from wkhelper.platform.tree_utils import format_leaf_label, iter_leaves_with_context
from wkhelper.ui.interface import UserInterface

logger = logging.getLogger(__name__)


class BasePlatform(ABC):
    """所有平台的抽象基类。"""

    def __init__(self, client: niquests.AsyncSession, ui: UserInterface):
        self.client = client
        self.ui = ui
        self.user: UserInfo | None = None

    @staticmethod
    def parse_cookie_string(raw: str) -> dict[str, str]:
        """解析浏览器 cookie 字符串为字典。

        Args:
            raw: 浏览器复制的 cookie 字符串 (e.g. "csrftoken=xxx; sessionid=yyy")

        Returns:
            dict: 解析后的 cookie 键值对。
        """
        result: dict[str, str] = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()
        return result

    @staticmethod
    def _parse_exercise_response(q_resp: dict[str, Any]) -> list[dict[str, Any]]:
        """解析习题 API 响应中的题目列表，兼容多种响应结构。"""
        if q_resp.get("success") is False:
            error_msg = q_resp.get("msg", "Unknown error")
            logger.warning(f"  ⚠️ API 返回错误: {error_msg}")
            if "blocked" in str(error_msg).lower():
                logger.warning("  🚫 请求被阻止（可能触发限流），建议稍后重试")
            return []

        logger.debug(f"  📡 响应键: {list(q_resp.keys())}")
        logger.debug(f"  📡 响应状态: success={q_resp.get('success')}, msg={str(q_resp.get('msg', ''))[:50]}")

        data = q_resp.get("data", q_resp)
        logger.debug(f"  📡 data 类型: {type(data).__name__}")
        if isinstance(data, dict):
            logger.debug(f"  📡 data 键: {list(data.keys())[:10]}")
            if "problems" in data:
                problems = data["problems"]
                logger.debug(f"  📡 从 data.problems 获取 {len(problems)} 道题目")
                return problems
        if isinstance(data, list):
            logger.debug(f"  📡 data 是列表，共 {len(data)} 项")
            return data
        logger.debug("  ❌ 无法解析题目列表，返回空列表")
        return []

    # ── 子类必须实现的抽象方法 ──

    @abstractmethod
    async def login(self, cookies: dict[str, str] | None = None) -> UserInfo:
        """执行登录并返回用户信息。"""
        ...

    @abstractmethod
    async def get_courses(self) -> list[Course]:
        """获取课程列表。"""
        ...

    @abstractmethod
    async def _get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        """获取课程章节树数据。"""
        ...

    @abstractmethod
    async def _get_leaf_schedules(self, course: Course) -> dict[int, float]:
        """获取课程叶子节点进度。"""
        ...

    @abstractmethod
    def _build_leaf_info_url(self, leaf_id: str | int, course: Course) -> str:
        """构建叶子节点详情的 API URL（不含 query string）。"""
        ...

    def _get_leaf_info_params(self, leaf_id: str | int, course: Course) -> dict[str, Any] | None:  # noqa: ARG002
        """构建叶子节点详情的 query 参数。默认无。"""
        return None

    @abstractmethod
    def _build_exercise_list_url(self, leaf_type_id: int | str) -> str:
        """构建习题列表的 API URL。"""
        ...

    @abstractmethod
    def _build_submit_url(self) -> str:
        """构建作业提交的 API URL。"""
        ...

    @abstractmethod
    def _build_submit_payload(self, problem_id: int, answer: list[str]) -> dict[str, Any]:
        """构建作业提交的请求体。"""
        ...

    # ── 子类可选覆盖的方法 ──

    def _get_request_kwargs(self, course: Course) -> dict[str, Any] | None:  # noqa: ARG002
        """返回请求平台 API 时附加的 kwargs（headers/cookies 等）。默认无附加。"""
        return None

    def _parse_submit_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """解析作业提交响应为统一的 {success, is_correct, correct_answer} 格式。"""
        if data.get("success") is True:
            result = data.get("data", {})
            return {
                "success": True,
                "is_correct": result.get("is_right", result.get("is_correct", False)),
                "correct_answer": result.get("answer", []),
            }
        return {"success": False, "is_correct": False, "correct_answer": []}

    # ── 模板方法：子类直接继承 ──

    async def _build_videos(self, course: Course) -> dict[int, str]:
        """从章节数据构建视频字典（模板方法）。"""
        data = await self._get_chapter_data(course)
        schedules = await self._get_leaf_schedules(course)
        videos: dict[int, str] = {}
        completed_video_ids: set[int] = set()
        for leaf, chapter_name, section_name in iter_leaves_with_context(data):
            if leaf.get("leaf_type") != 0 or not leaf.get("is_show", True):
                continue
            leaf_id = leaf.get("id")
            if leaf_id is None:
                continue
            video_id = int(leaf_id)
            is_completed = schedules.get(video_id, 0.0) >= 1.0
            if is_completed:
                completed_video_ids.add(video_id)

            label = format_leaf_label(leaf, chapter_name, section_name)
            if is_completed:
                label = f"{label} [已完成]"
            videos[video_id] = label

        course.metadata["completed_video_ids"] = completed_video_ids
        return videos

    async def _build_homeworks(self, course: Course) -> list[Homework]:
        """从章节数据构建作业列表（模板方法）。"""
        data = await self._get_chapter_data(course)
        schedules = await self._get_leaf_schedules(course)
        homeworks: list[Homework] = []
        for leaf, chapter_name, section_name in iter_leaves_with_context(data):
            if leaf.get("leaf_type") != 6 or not leaf.get("is_show", True):
                continue
            leaf_id = int(leaf["id"])
            is_completed = schedules.get(leaf_id, 0.0) >= 1.0
            label = format_leaf_label(leaf, chapter_name, section_name)
            if is_completed:
                label = f"{label} [已完成]"
            homeworks.append(
                Homework(
                    id=leaf["id"],
                    name=label,
                    deadline=leaf.get("score_deadline"),
                    metadata={
                        "chapter_id": leaf.get("chapter_id"),
                        "chapter_name": chapter_name,
                        "section_name": section_name,
                        "is_score": leaf.get("is_score"),
                        "is_assessed": leaf.get("is_assessed"),
                        "is_completed": is_completed,
                        "start_time": leaf.get("start_time"),
                    },
                )
            )
        return homeworks

    async def get_leaf_questions(self, leaf_id: str | int, course: Course) -> list[dict[str, Any]]:
        """获取叶子节点的题目列表（模板方法）。"""
        url = self._build_leaf_info_url(leaf_id, course)
        kwargs = self._get_request_kwargs(course) or {}
        params = self._get_leaf_info_params(leaf_id, course)
        if params:
            kwargs.setdefault("params", {}).update(params)
        resp_obj = await self.client.get(url, **kwargs)
        resp = resp_obj.json()
        leaf_type_id = resp["data"]["content_info"]["leaf_type_id"]

        q_url = self._build_exercise_list_url(leaf_type_id)
        q_resp_obj = await self.client.get(q_url, **kwargs)
        return self._parse_exercise_response(q_resp_obj.json())

    async def get_videos(self, course: Course) -> dict[int, str]:
        """获取课程视频列表。"""
        return await self._build_videos(course)

    async def get_homeworks(self, course: Course) -> list[Homework]:
        """获取课程作业列表。"""
        return await self._build_homeworks(course)

    # ── 作业处理 ──

    async def _submit_answer(
        self,
        homework_name: str,
        problem_id: int,
        answer: list[str],
        client: niquests.AsyncSession,
        kwargs: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """提交单个题目答案，含限流重试逻辑。"""
        url = self._build_submit_url()
        if isinstance(answer, str):
            answer = [answer]
        payload = self._build_submit_payload(problem_id, answer)

        while True:
            response = await client.post(url, json=payload, **(kwargs or {}))
            match = re.search(r"Expected available in(.+?)second.", response.text)
            if match:
                delay_time = float(match.group(1).strip())
                remain = max(1, math.ceil(delay_time))
                while remain > 0:
                    self.ui.update_homework_status(homework_name, f"⚠️ 限流，等待 {remain}s")
                    await asyncio.sleep(1)
                    remain -= 1
                self.ui.update_homework_status(homework_name, "🔄 重试中...")
                self.ui.update_homework_status(homework_name, "🧠 答题中")
                continue
            return self._parse_submit_response(response.json())

    @abstractmethod
    async def do_video(self, video_id: str, video_name: str, course: Course) -> None:
        """观看单个视频。"""
        ...

    @abstractmethod
    async def do_homework(self, homework: Homework, course: Course, is_random: bool = False) -> None:
        """完成单个作业。"""
        ...
