"""平台基础接口。"""

import asyncio
import logging
import math
import random
import re
from abc import ABC, abstractmethod
from typing import Any

import niquests

from wkhelper.core.homework import generic_random_answer, generic_submit_homework
from wkhelper.core.models import Course, Homework, UserInfo, VideoContext
from wkhelper.core.video import generic_watch_video
from wkhelper.platform.tree_utils import format_leaf_label, iter_leaves_with_context
from wkhelper.ui.interface import UserInterface

logger = logging.getLogger(__name__)


class BasePlatform(ABC):
    """所有平台的抽象基类。"""

    def __init__(self, client: niquests.AsyncSession, ui: UserInterface):
        self.client = client
        self.ui = ui
        self.user: UserInfo | None = None
        self.current_cookies: dict[str, str] | None = None
        self._submit_ctx: dict[str, Any] = {}

    async def _request_json(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        """通用的发起 HTTP 请求并解析 JSON 的方法，包含状态码检查。"""
        resp = await self.client.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp.json()

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
            logger.debug(f"  📡 data 键: {list(data.keys())}")
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

    async def login(self, cookies: dict[str, str] | None = None) -> UserInfo:
        """执行登录并返回用户信息。"""
        if cookies is not None:
            return await self._login_with_cookies(cookies)
        return await self._login_with_qrcode()

    @abstractmethod
    async def _login_with_cookies(self, cookies: dict[str, str]) -> UserInfo:
        """使用 Cookie 登录。"""
        ...

    @abstractmethod
    async def _login_with_qrcode(self) -> UserInfo:
        """使用扫码登录。"""
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
        resp = await self._request_json("GET", url, **kwargs)
        leaf_type_id = resp["data"]["content_info"]["leaf_type_id"]

        q_url = self._build_exercise_list_url(leaf_type_id)
        resp_json = await self._request_json("GET", q_url, **kwargs)
        problems = self._parse_exercise_response(resp_json)

        # 反混淆处理
        data = resp_json.get("data")
        if isinstance(data, dict):
            font_url = data.get("font")
            if font_url:
                from wkhelper.core.deobfuscator import deobfuscate_questions

                await deobfuscate_questions(problems, font_url, self.client)

        return problems

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

        max_retries = 10
        for _ in range(max_retries):
            response = await client.post(url, json=payload, **(kwargs or {}))
            if response.text:
                match = re.search(r"Expected available in(.+?)second.", response.text)
            else:
                match = None
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
            response.raise_for_status()
            return self._parse_submit_response(response.json())

        raise RuntimeError(f"题目 {problem_id} 提交重试次数过多，可能遭遇持续限流")

    @abstractmethod
    async def _prepare_video_context(self, video_id: str, course: Course) -> VideoContext:
        """准备视频观看所需的平台上下文信息。"""
        ...

    def _build_heartbeat_payload(
        self,
        video_id: str,
        classroom_id: str,
        user_id: int,
        course_id: int,
        sku_id: int,
        video_frame: int,
        i: int,
        timestamp: int,
    ) -> dict[str, Any]:
        """构建心跳请求载荷。默认实现适用于两个平台。"""
        return {
            "i": i,
            "et": "heartbeat",
            "p": "web",
            "n": "ali-cdn.xuetangx.com",
            "lob": "ykt",
            "cp": video_frame,
            "fp": 0,
            "tp": 0,
            "sp": 2,
            "ts": str(timestamp),
            "u": int(user_id),
            "uip": "",
            "c": int(course_id),
            "v": int(video_id),
            "skuid": int(sku_id),
            "classroomid": str(classroom_id),
            "cc": str(video_id),
            "d": 4976.5,
            "pg": f"{video_id}_{''.join(random.sample('abcdefghijklmnopqrstuvwxyz0123456789', 4))}",
            "sq": i,
            "t": "video",
        }

    async def do_video(self, video_id: str, video_name: str, course: Course) -> None:
        """观看单个视频（模板方法）。"""
        ctx = await self._prepare_video_context(video_id, course)

        await generic_watch_video(
            self.client,
            ctx.video_id,
            video_name,
            ctx.classroom_id,
            ctx.user_id,
            ctx.course_id,
            ctx.sku_id,
            ctx.progress_url,
            ctx.heartbeat_url,
            self._build_heartbeat_payload,
            request_kwargs=ctx.request_kwargs,
            on_progress=self.ui.update_video_progress,
            on_complete=self.ui.finish_video_progress,
            on_status=self.ui.update_video_status,
        )

    @abstractmethod
    async def _prepare_submit_context(self, homework: Homework, course: Course) -> None:
        """准备提交上下文（设置 self._submit_ctx）。

        子类在这里设置平台特有的提交字段，例如 classroom_id、leaf_id、sign 等。
        """
        ...

    async def do_homework(self, homework: Homework, course: Course, is_random: bool = False) -> None:
        """完成单个作业（模板方法）。

        编排流程：获取题目 → 准备提交上下文 → 提交已知答案 → AI 兜底 → 更新状态。
        子类通过 _prepare_submit_context() 注入平台差异。
        """
        self.ui.update_homework_status(homework.name, "🧠 答题中")
        try:
            questions = await self.get_leaf_questions(homework.id, course)
            await self._prepare_submit_context(homework, course)
            kwargs = self._get_request_kwargs(course)

            total = len(questions)
            self.ui.update_homework_progress(homework.name, 0, total)

            def _on_progress(done: int, total: int) -> None:
                self.ui.update_homework_progress(homework.name, done, total)

            async def submit_func(problem_id, answer, course_info, client, kwargs):
                return await self._submit_answer(homework.name, problem_id, answer, client, kwargs)

            if is_random:
                await generic_random_answer(questions, submit_func, None, self.client, headers=kwargs, on_progress=_on_progress)
            else:
                from wkhelper.solver import LocalDbSolver

                solvers = [LocalDbSolver()]
                resolved_pairs = []
                unresolved = questions.copy()

                for solver in solvers:
                    if not unresolved:
                        break
                    if solver is None:
                        continue

                    resolved_for_solver = await solver.batch_solve(unresolved, self.ui)

                    for q, ans in resolved_for_solver:
                        if ans.selected_options:
                            resolved_pairs.append((q, ans.selected_options))
                            # 安全地从 unresolved 列表中移除已解答的题目
                            # q 需要用对应的 key 或者直接尝试 remove
                            try:
                                unresolved.remove(q)
                            except ValueError:
                                pass

                if unresolved:
                    self.ui.print_message(f"[yellow]⚠️ 还有 {len(unresolved)} 道题目无法解答，将跳过。[/]")

                if resolved_pairs:
                    self.ui.update_homework_status(homework.name, "🧠 提交答案")
                    await generic_submit_homework(resolved_pairs, submit_func, None, self.client, headers=kwargs, on_progress=_on_progress)

            self.ui.update_homework_status(homework.name, "✅ 完成")
        except Exception:
            self.ui.update_homework_status(homework.name, "❌ 失败")
            raise
        finally:
            self.ui.finish_homework_progress(homework.name)
