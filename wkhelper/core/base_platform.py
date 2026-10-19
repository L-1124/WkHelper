"""平台基础接口与课件树抽象。"""

import asyncio
import logging
import math
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from typing import Any

import niquests

from wkhelper.core.events import emit_event, is_debug_enabled
from wkhelper.core.exceptions import APIError
from wkhelper.core.homework import generic_submit_homework
from wkhelper.core.models import (
    ArticleItem,
    ArticleReadResult,
    Course,
    DiscussionCommentResult,
    DiscussionItem,
    DiscussionTopicDetail,
    Homework,
    HomeworkSubmitResult,
    JsonDict,
    JsonValue,
    QuestionSubmitOutcome,
    QuestionSubmitResult,
    UserInfo,
    VideoContext,
    VideoProgress,
    VideoWatchResult,
)
from wkhelper.core.video import VideoWatchOptions, generic_watch_video, parse_progress_text

logger = logging.getLogger(__name__)

type LeafContext = tuple[dict[str, Any], str, str | None]


class BasePlatformParser:
    """平台通用树结构与响应解析器。"""

    @staticmethod
    def format_leaf_label(
        leaf: dict[str, Any],
        chapter_name: str,
        section_name: str | None,
    ) -> str:
        """构建树形展示标签，避免同名任务导致选择歧义。"""
        raw_name = str(leaf.get("name") or "未命名任务").strip()
        if raw_name.lower() == "video" and section_name:
            raw_name = section_name

        parts = [chapter_name]
        if section_name and section_name != chapter_name:
            parts.append(section_name)
        parts.append(raw_name)
        return f"{' / '.join(parts)} (ID:{leaf.get('id')})"

    @staticmethod
    def iter_leaves_with_context(chapter_data: list[dict[str, Any]]) -> Iterator[LeafContext]:
        """递归遍历课件树，支持单层、多层嵌套及混合结构，保留完整章节上下文。"""

        def _traverse(node: dict[str, Any], chapter: str, section: str | None) -> Iterator[LeafContext]:
            name = str(node.get("name") or "").strip()
            children = node.get("section_leaf_list") or node.get("section_list") or node.get("leaf_list") or node.get("units")

            if isinstance(children, list) and children:
                curr_chapter = chapter or name or "未命名章节"
                curr_section = name if chapter else section
                for child in children:
                    if isinstance(child, dict):
                        yield from _traverse(child, curr_chapter, curr_section)
            else:
                yield node, chapter or "未命名章节", section

        for root_item in chapter_data:
            if not isinstance(root_item, dict):
                continue
            ch_name = str(root_item.get("name") or "未命名章节")
            children = (
                root_item.get("section_leaf_list") or root_item.get("section_list") or root_item.get("leaf_list") or root_item.get("units")
            )
            if isinstance(children, list) and children:
                for sub in children:
                    if isinstance(sub, dict):
                        yield from _traverse(sub, ch_name, sub.get("name"))
            elif "leaf_type" in root_item:
                yield root_item, ch_name, None

    @staticmethod
    def parse_cookie_string(raw: str) -> dict[str, str]:
        """解析浏览器 cookie 字符串为字典。"""
        result: dict[str, str] = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()
        return result

    @staticmethod
    def parse_exercise_response(q_resp: dict[str, Any]) -> list[dict[str, Any]]:
        """解析习题 API 响应中的题目列表，兼容多种响应结构。"""
        if q_resp.get("success") is False:
            error_msg = q_resp.get("msg", "Unknown error")
            logger.warning(f"API 返回错误: {error_msg}")
            return []

        data = q_resp.get("data", q_resp)
        if isinstance(data, dict):
            if "problems" in data:
                return data["problems"]
        if isinstance(data, list):
            return data
        return []


class BasePlatform(ABC):
    """所有平台的抽象基类。"""

    def __init__(self, client: niquests.AsyncSession) -> None:
        self.client = client
        self.user: UserInfo | None = None
        self.current_cookies: dict[str, str] | None = None

    async def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """通用的发起 HTTP 请求并解析 JSON 的方法，包含状态码检查。"""
        start = time.perf_counter()
        resp = None
        try:
            resp = await self.client.request(method, url, **kwargs)
            resp.raise_for_status()
            if is_debug_enabled():
                duration_ms = round((time.perf_counter() - start) * 1000, 2)
                emit_event(
                    "http.trace",
                    {
                        "method": method.upper(),
                        "url": url,
                        "status_code": resp.status_code,
                        "duration_ms": duration_ms,
                    },
                )
            return resp.json()
        except Exception as exc:
            if is_debug_enabled():
                duration_ms = round((time.perf_counter() - start) * 1000, 2)
                status_code = getattr(resp, "status_code", None)
                emit_event(
                    "http.trace",
                    {
                        "method": method.upper(),
                        "url": url,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                        "error": str(exc),
                    },
                )
            raise

    @staticmethod
    def parse_cookie_string(raw: str) -> dict[str, str]:
        """解析浏览器 cookie 字符串为字典。"""
        return BasePlatformParser.parse_cookie_string(raw)

    @staticmethod
    def _parse_exercise_response(q_resp: dict[str, Any]) -> list[dict[str, Any]]:
        """解析习题 API 响应中的题目列表，兼容多种响应结构。"""
        return BasePlatformParser.parse_exercise_response(q_resp)

    # 平台抽象方法

    async def login(
        self,
        cookies: dict[str, str] | None = None,
        display: str = "image",
        open_browser: bool = False,
        timeout: int = 120,
    ) -> UserInfo:
        """执行登录并返回用户信息。"""
        if cookies is not None:
            return await self._login_with_cookies(cookies)
        return await self._login_with_qrcode(display=display, open_browser=open_browser, timeout=timeout)

    @abstractmethod
    async def _login_with_cookies(self, cookies: dict[str, str]) -> UserInfo: ...

    @abstractmethod
    async def _login_with_qrcode(
        self,
        display: str = "image",
        open_browser: bool = False,
        timeout: int = 120,
    ) -> UserInfo: ...

    @abstractmethod
    async def get_courses(self) -> list[Course]: ...

    @abstractmethod
    async def _get_chapter_data(self, course: Course) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def _get_leaf_schedules(self, course: Course) -> dict[int, float]: ...

    @abstractmethod
    def _build_leaf_info_url(self, leaf_id: str | int, course: Course) -> str: ...

    def _get_leaf_info_params(self, leaf_id: str | int, course: Course) -> dict[str, Any] | None:
        return None

    @abstractmethod
    def _build_exercise_list_url(self, leaf_type_id: int | str) -> str: ...

    @abstractmethod
    def _build_submit_url(self) -> str: ...

    @abstractmethod
    def _build_submit_payload(self, problem_id: int, answer: list[str], submit_ctx: dict[str, Any] | None = None) -> dict[str, Any]: ...

    def _get_request_kwargs(self, course: Course) -> dict[str, Any] | None:
        return None

    async def _get_completed_video_ids(self, course: Course) -> set[int] | None:
        """返回平台权威的「已完成视频 ID」集合。

        返回 `None` 表示平台没有更可靠的来源，基类退回用 `_get_leaf_schedules`
        的章节进度值判断。平台实现不要用空集合表示"未知"。
        """
        return None

    @staticmethod
    def _normalize_answer(raw: Any) -> list[str]:
        """把平台返回的答案值统一为字符串列表。"""
        if isinstance(raw, list):
            return [str(item) for item in raw]
        if raw is None:
            return []
        return [str(raw)]

    def _parse_submit_response(self, data: dict[str, Any]) -> QuestionSubmitOutcome:
        """解析作业提交响应。"""
        if data.get("success") is True:
            result = data.get("data", {})
            return QuestionSubmitOutcome(
                success=True,
                is_correct=bool(result.get("is_right", result.get("is_correct", False))),
                correct_answer=self._normalize_answer(result.get("answer", [])),
            )
        msg = data.get("msg") or data.get("message") or "平台返回提交失败"
        code = data.get("error_code")
        error_msg = f"{msg} (error_code: {code})" if code is not None else str(msg)
        return QuestionSubmitOutcome(
            success=False,
            is_correct=False,
            correct_answer=[],
            error=error_msg,
            error_code=code if isinstance(code, int) else None,
        )

    # 模板方法

    async def get_videos(self, course: Course) -> dict[int, str]:
        """从章节数据构建视频字典。"""
        data = await self._get_chapter_data(course)
        # 优先使用平台权威的「已完成视频集合」；没有时退回章节进度值。
        authoritative_completed = await self._get_completed_video_ids(course)
        schedules = {} if authoritative_completed is not None else await self._get_leaf_schedules(course)
        videos: dict[int, str] = {}
        completed_video_ids: set[int] = set()
        for leaf, chapter_name, section_name in BasePlatformParser.iter_leaves_with_context(data):
            if leaf.get("leaf_type") not in (0, 1, 2, 8) or not leaf.get("is_show", True):
                continue
            leaf_id = leaf.get("id")
            if leaf_id is None:
                continue
            video_id = int(leaf_id)
            is_completed = (
                video_id in authoritative_completed if authoritative_completed is not None else schedules.get(video_id, 0.0) >= 1.0
            )
            if is_completed:
                completed_video_ids.add(video_id)

            label = BasePlatformParser.format_leaf_label(leaf, chapter_name, section_name)
            if is_completed:
                label = f"{label} [已完成]"
            videos[video_id] = label

        course.metadata["completed_video_ids"] = completed_video_ids
        return videos

    async def get_homeworks(self, course: Course) -> list[Homework]:
        """获取课程作业列表。"""
        data = await self._get_chapter_data(course)
        schedules = await self._get_leaf_schedules(course)
        homeworks: list[Homework] = []

        for leaf, chapter_name, section_name in BasePlatformParser.iter_leaves_with_context(data):
            leaf_type = leaf.get("leaf_type")
            if leaf_type not in (5, 6, 7, 9) or not leaf.get("is_show", True):
                continue
            leaf_id = leaf.get("id")
            if leaf_id is None:
                continue

            label = BasePlatformParser.format_leaf_label(leaf, chapter_name, section_name)
            is_completed = schedules.get(int(leaf_id), 0.0) >= 1.0

            url = self._build_leaf_info_url(leaf_id, course)
            params = self._get_leaf_info_params(leaf_id, course)
            deadline = None

            try:
                resp = await self._request_json("GET", url, params=params)
                leaf_info = resp.get("data", {}).get("content_info", {})
                deadline = leaf_info.get("end") or leaf_info.get("deadline")
            except Exception as e:
                logger.debug(f"获取叶子节点 {leaf_id} 详情失败: {e}")

            homeworks.append(
                Homework(
                    id=str(leaf_id),
                    name=label,
                    deadline=deadline,
                    completed=is_completed,
                    metadata={"is_completed": is_completed},
                )
            )
        return homeworks

    async def get_articles(self, course: Course) -> list[ArticleItem]:
        """获取课程资料/图文节点列表。"""
        data = await self._get_chapter_data(course)
        schedules = await self._get_leaf_schedules(course)
        articles: list[ArticleItem] = []
        for leaf, chapter_name, section_name in BasePlatformParser.iter_leaves_with_context(data):
            if leaf.get("leaf_type") != 3 or not leaf.get("is_show", True):
                continue
            leaf_id = leaf.get("id")
            if leaf_id is None:
                continue
            label = BasePlatformParser.format_leaf_label(leaf, chapter_name, section_name)
            is_completed = schedules.get(int(leaf_id), 0.0) >= 1.0
            articles.append(ArticleItem(id=str(leaf_id), name=label, completed=is_completed))
        return articles

    async def _mark_article_finish(self, leaf_id: str | int, course: Course) -> bool:
        raise NotImplementedError

    async def read_article(self, leaf_id: str | int, course: Course) -> ArticleReadResult:
        """标记指定资料/图文节点为已读已完成。"""
        articles = await self.get_articles(course)
        target = next((item for item in articles if item.id == str(leaf_id)), None)
        title = target.name if target else str(leaf_id)
        success = await self._mark_article_finish(leaf_id, course)
        return ArticleReadResult(
            course_id=str(course.id),
            leaf_id=str(leaf_id),
            name=title,
            success=success,
            completed=True,
        )

    async def get_discussions(self, course: Course) -> list[DiscussionItem]:
        """获取课程讨论题列表。"""
        data = await self._get_chapter_data(course)
        schedules = await self._get_leaf_schedules(course)
        discussions: list[DiscussionItem] = []
        for leaf, chapter_name, section_name in BasePlatformParser.iter_leaves_with_context(data):
            if leaf.get("leaf_type") not in (4, 10) or not leaf.get("is_show", True):
                continue
            leaf_id = leaf.get("id")
            if leaf_id is None:
                continue
            label = BasePlatformParser.format_leaf_label(leaf, chapter_name, section_name)
            is_completed = schedules.get(int(leaf_id), 0.0) >= 1.0
            discussions.append(DiscussionItem(id=str(leaf_id), name=label, completed=is_completed))
        return discussions

    async def get_discussion_detail(self, leaf_id: str | int, course: Course) -> DiscussionTopicDetail:
        raise NotImplementedError

    async def submit_discussion(self, leaf_id: str | int, course: Course, content: str) -> DiscussionCommentResult:
        raise NotImplementedError

    async def get_leaf_questions(self, leaf_id: str | int, course: Course) -> list[dict[str, Any]]:
        """获取指定叶子节点的习题列表。"""
        url = self._build_leaf_info_url(leaf_id, course)
        params = self._get_leaf_info_params(leaf_id, course)
        kwargs = self._get_request_kwargs(course) or {}
        resp = await self._request_json("GET", url, params=params, **kwargs)
        content_info = resp.get("data", {}).get("content_info", {})
        leaf_type_id = content_info.get("leaf_type_id") or resp.get("data", {}).get("leaf_type_id")
        if not leaf_type_id:
            return []

        ex_url = self._build_exercise_list_url(leaf_type_id)
        q_resp = await self._request_json("GET", ex_url, **kwargs)
        questions = self._parse_exercise_response(q_resp)
        data = q_resp.get("data", {})
        if isinstance(data, dict):
            font_url = data.get("font")
            if font_url and questions:
                try:
                    from wkhelper.core.deobfuscator import deobfuscate_questions

                    await deobfuscate_questions(questions, font_url, self.client)
                except Exception as e:
                    logger.debug(f"反混淆题目失败: {e}")
        return questions

    async def _submit_answer(
        self,
        homework_name: str,
        problem_id: int,
        answer: list[str] | str,
        client: niquests.AsyncSession,
        kwargs: dict[str, Any] | None = None,
        submit_ctx: dict[str, Any] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> QuestionSubmitOutcome:
        """提交单个题目答案，含限流重试。"""
        url = self._build_submit_url()
        if isinstance(answer, str):
            answer = [answer]
        payload = self._build_submit_payload(problem_id, answer, submit_ctx=submit_ctx)

        max_retries = 10
        for _ in range(max_retries):
            req_start = time.perf_counter()
            response = await client.post(url, json=payload, **(kwargs or {}))
            if is_debug_enabled():
                duration_ms = round((time.perf_counter() - req_start) * 1000, 2)
                emit_event(
                    "http.trace",
                    {
                        "method": "POST",
                        "url": url,
                        "problem_id": problem_id,
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                    },
                )
            if response.text:
                match = re.search(r"Expected available in(.+?)second.", response.text)
            else:
                match = None
            if match:
                delay_time = float(match.group(1).strip())
                remain = max(1, math.ceil(delay_time))
                if on_status:
                    on_status(f"限流等待 {remain}s")
                await asyncio.sleep(remain)
                continue
            response.raise_for_status()
            parsed = response.json()
            if not isinstance(parsed, dict):
                raise APIError(f"题目 {problem_id} 提交返回结构无法识别")
            return self._parse_submit_response(parsed)

        raise RuntimeError(f"题目 {problem_id} 提交重试次数过多")

    @abstractmethod
    async def _prepare_video_context(self, video_id: str, course: Course) -> VideoContext: ...

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
    ) -> JsonDict:
        """构建心跳请求载荷。"""
        import random

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

    async def do_video(
        self,
        video_id: str,
        video_name: str,
        course: Course,
        options: VideoWatchOptions | None = None,
        on_event: Callable[[str, dict[str, JsonValue]], None] | None = None,
    ) -> VideoWatchResult:
        """观看单个视频。"""
        ctx = await self._prepare_video_context(video_id, course)
        opts = options or VideoWatchOptions()

        return await generic_watch_video(
            client=self.client,
            video_id=ctx.video_id,
            video_name=video_name,
            classroom_id=ctx.classroom_id,
            user_id=ctx.user_id,
            course_id=ctx.course_id,
            sku_id=ctx.sku_id,
            progress_url=ctx.progress_url,
            heartbeat_url=ctx.heartbeat_url,
            payload_gen=self._build_heartbeat_payload,
            request_kwargs=ctx.request_kwargs,
            options=opts,
            on_event=on_event,
        )

    async def get_video_progress(self, video_id: str, course: Course) -> VideoProgress:
        """查询单视频服务端进度与完成标识。"""
        vctx = await self._prepare_video_context(video_id, course)
        if vctx.request_kwargs is not None:
            resp = await self.client.get(vctx.progress_url, **vctx.request_kwargs)
        else:
            resp = await self.client.get(vctx.progress_url)
        resp.raise_for_status()
        text = resp.text or ""
        rate, _ = parse_progress_text(text, str(video_id))
        return VideoProgress(
            course_id=str(course.id),
            video_id=str(video_id),
            current_rate=rate,
            completed='"completed":1' in text or rate >= 1.0,
        )

    @abstractmethod
    async def _prepare_submit_context(self, homework: Homework, course: Course) -> dict[str, Any]: ...

    async def do_homework(
        self,
        homework: Homework,
        course: Course,
        answers: dict[str, list[str]] | None = None,
        on_event: Callable[[str, dict[str, JsonValue]], None] | None = None,
        concurrency: int = 1,
        delay: float = 0.0,
        delay_jitter: float = 0.0,
        random_order: bool = False,
    ) -> HomeworkSubmitResult:
        """完成单个作业，提交显式答案集合并返回结构化结果。"""
        questions = await self.get_leaf_questions(homework.id, course)
        submit_ctx = await self._prepare_submit_context(homework, course)
        kwargs = self._get_request_kwargs(course)

        if answers is None:
            raise ValueError("必须显式提供作业答案集合以供提交")

        resolved_pairs: list[tuple[dict[str, Any], list[str]]] = []
        for q in questions:
            pid = str(q.get("problem_id") or q.get("id"))
            if pid in answers:
                resolved_pairs.append((q, answers[pid]))

        async def submit_func(
            problem_id: int, answer: list[str], course_info: Course, client: niquests.AsyncSession, kwargs: dict[str, Any] | None
        ) -> QuestionSubmitOutcome:
            return await self._submit_answer(
                homework.name,
                problem_id,
                answer,
                client,
                kwargs=kwargs,
                submit_ctx=submit_ctx,
            )

        return await generic_submit_homework(
            resolved_pairs=resolved_pairs,
            submit_func=submit_func,
            course_info=course,
            client=self.client,
            headers=kwargs,
            on_event=on_event,
            concurrency=concurrency,
            delay=delay,
            delay_jitter=delay_jitter,
            random_order=random_order,
        )

    async def submit_question(
        self,
        homework: Homework,
        course: Course,
        question_id: str | int,
        answer: list[str] | str,
    ) -> QuestionSubmitResult:
        """提交单个题目的答案并返回结果。"""
        submit_ctx = await self._prepare_submit_context(homework, course)
        kwargs = self._get_request_kwargs(course)
        ans_list = [answer] if isinstance(answer, str) else list(answer)
        try:
            pid = int(question_id)
        except (ValueError, TypeError):
            raise ValueError(f"题目 ID 必须为有效整数: {question_id}") from None

        res = await self._submit_answer(
            homework.name,
            pid,
            ans_list,
            self.client,
            kwargs=kwargs,
            submit_ctx=submit_ctx,
        )
        return QuestionSubmitResult(
            question_id=str(question_id),
            submitted_answer=ans_list,
            success=res.success,
            is_correct=res.is_correct,
            correct_answer=res.correct_answer,
            error=res.error,
        )
