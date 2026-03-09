"""雨课堂平台实现。"""

import asyncio
import logging
import math
import random
import re
from typing import Any, override

import httpx
import qrcode
from httpx_ws import aconnect_ws
from terminal_qrcode import draw

from wkhelper.core.config import DEFAULT_HEADERS
from wkhelper.core.exceptions import APIError, AuthError
from wkhelper.core.homework import generic_process_homework, generic_random_answer
from wkhelper.core.models import Course, Homework, UserInfo
from wkhelper.core.video import generic_watch_video
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.tree_utils import format_leaf_label, iter_leaves_with_context

logger = logging.getLogger(__name__)


def render_login_qrcode(payload: str, *, renderer: str = "auto") -> str:
    """将登录字符串渲染为终端二维码。"""
    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    return str(draw(qr.get_matrix(), renderer=renderer))


class YuketangPlatform(BasePlatform):
    """雨课堂平台。"""

    @override
    async def login(self) -> UserInfo:
        """登录雨课堂。

        通过 WebSocket 请求登录二维码，并在本地判定超时后主动重新请求。

        Returns:
            UserInfo: 包含 Auth 和 UserID 的登录信息。

        Raises:
            AuthError: 当 WebSocket 连接失败或最终未获取到完整登录信息时抛出。
        """

        logger.info("🔐 正在获取雨课堂 Cookie...")
        login_data = {}

        # 1. 提取特定的请求负载
        request_payload = {
            "op": "requestlogin",
            "role": "web",
            "version": 1.4,
            "type": "qrcode",
        }

        try:
            async with aconnect_ws("wss://www.yuketang.cn/wsapp/", self.client) as ws:
                # 首次请求
                await ws.send_json(request_payload)

                # 设定默认超时变量 (需确认服务端实际的过期策略)
                timeout_seconds = 60.0

                while True:
                    try:
                        # 2. 引入带时效控制的读取
                        message = await asyncio.wait_for(ws.receive_json(), timeout=timeout_seconds)

                        # 补充点：如果雨课堂的 message 中也存在类似 expire_seconds 的字段，应在此处动态更新 timeout_seconds
                        # if "expire_seconds" in message:
                        #     timeout_seconds = float(message["expire_seconds"])

                        if "qrcode" in message and message["qrcode"]:
                            print(render_login_qrcode(message["qrcode"]))
                            logger.info(f"请使用雨课堂扫码登录 (判定有效时间: {int(timeout_seconds)}秒)...")

                        if message.get("op") == "loginsuccess":
                            login_data.update(message)
                            break

                    except TimeoutError:
                        # 3. 超时异常触发主动重新请求
                        logger.warning("⏳ 二维码可能已过期，正在重新请求...")
                        await ws.send_json(request_payload)

        except Exception as e:
            logger.error(f"❌ WebSocket 连接失败: {e}")
            raise AuthError("WebSocket 连接失败") from e

        if "Auth" not in login_data or "UserID" not in login_data:
            logger.error("❌ 登录失败，未获取到登录信息")
            raise AuthError("登录失败")

        # 1. 换取 Web Login Cookie
        response = await self.client.post(
            "https://www.yuketang.cn/pc/web_login",
            json={"Auth": login_data["Auth"], "UserID": str(login_data["UserID"])},
            headers=DEFAULT_HEADERS,
        )

        cookies = {
            "csrftoken": response.cookies.get("csrftoken") or "",
            "sessionid": response.cookies.get("sessionid") or "",
        }

        if not cookies["csrftoken"] or not cookies["sessionid"]:
            logger.error("❌ Cookie 获取失败！")
            raise AuthError("Cookie 获取失败")

        logger.info("✅ Cookie 获取成功！")

        self.client.headers.update(
            {
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
                "Content-Type": "application/json",
                "Referer": "https://www.yuketang.cn/",
                "X-CSRFToken": cookies["csrftoken"],
                "Xtbz": "ykt",
            }
        )
        self.client.cookies.update(cookies)

        # 2. 获取用户信息
        resp = await self.client.get("https://www.yuketang.cn/api/v3/user/basic-info")
        data = resp.json()
        if data["code"] != 0:
            raise APIError("获取用户信息失败")

        info = data["data"]
        self.user = UserInfo(id=info["id"], name=info["name"], school=info.get("school"))
        return self.user

    @override
    async def get_courses(self) -> list[Course]:
        url = "https://www.yuketang.cn/v2/api/web/courses/list?identity=2"
        resp_obj = await self.client.get(url)
        resp = resp_obj.json()
        if resp["errcode"] != 0:
            raise APIError("获取课程列表失败")

        return [
            Course(
                id=c["course"]["id"],
                name=c["course"]["name"],
                platform_id="ykt",
                metadata={
                    "classroom_id": c["classroom_id"],
                    "university_id": c["course"]["university_id"],
                },
            )
            for c in resp["data"]["list"]
        ]

    def _get_course_kwargs(self, course: Course) -> dict[str, Any]:
        cid = str(course.metadata["classroom_id"])
        uid = str(course.metadata["university_id"])
        return {
            "headers": {
                "classroom-id": cid,
                "Xtbz": "ykt",
            },
            "cookies": {
                "xtbz": "ykt",
                "platform_type": "1",
                "uv_id": uid,
                "university_id": uid,
                "platform_id": "3",
                "classroom_id": cid,
                "classroomID": cid,
            },
        }

    async def _get_classroom_info(self, course: Course) -> dict[str, Any]:
        cid = course.metadata["classroom_id"]
        url = f"https://www.yuketang.cn/v2/api/web/classrooms/{cid}?role=5"
        kwargs = self._get_course_kwargs(course)
        resp_obj = await self.client.get(url, **kwargs)
        resp = resp_obj.json()
        if resp["errcode"] != 0:
            raise APIError("获取课堂信息失败")
        return resp["data"]

    async def _get_chapter_info(self, course: Course) -> list[dict[str, Any]]:
        info = await self._get_classroom_info(course)
        course.metadata["sign"] = info.get("course_sign", "")
        course.metadata["free_sku_id"] = info.get("free_sku_id")
        course.metadata["course_id"] = info.get("course_id")

        cid = course.metadata["classroom_id"]
        uid = course.metadata["university_id"]
        sign = course.metadata["sign"]

        url = (
            f"https://www.yuketang.cn/mooc-api/v1/lms/learn/course/chapter?cid={cid}&sign={sign}&term=latest&uv_id={uid}&classroom_id={cid}"
        )
        kwargs = self._get_course_kwargs(course)
        resp_obj = await self.client.get(url, **kwargs)
        resp = resp_obj.json()
        return resp["data"]["course_chapter"]

    async def _get_leaf_schedules(self, course: Course) -> dict[int, float]:
        """获取课程叶子节点进度。"""
        cid = course.metadata["classroom_id"]
        uid = course.metadata["university_id"]
        sign = course.metadata.get("sign", "")
        kwargs = self._get_course_kwargs(course)
        url = (
            "https://www.yuketang.cn/mooc-api/v1/lms/learn/course/schedule"
            f"?cid={cid}&sign={sign}&term=latest&uv_id={uid}&classroom_id={cid}"
        )
        resp_obj = await self.client.get(url, **kwargs)
        resp = resp_obj.json()
        if not resp.get("success"):
            logger.warning("⚠️ 获取课程进度失败，默认按未完成处理")
            return {}

        raw = resp.get("data", {}).get("leaf_schedules", {})
        schedules: dict[int, float] = {}
        for leaf_id, progress in raw.items():
            try:
                schedules[int(leaf_id)] = float(progress)
            except (TypeError, ValueError):
                continue
        return schedules

    @override
    async def get_videos(self, course: Course) -> dict[int, str]:
        chapters = await self._get_chapter_info(course)
        schedules = await self._get_leaf_schedules(course)
        videos: dict[int, str] = {}
        completed_video_ids: set[int] = set()
        for leaf, chapter_name, section_name in iter_leaves_with_context(chapters):
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

    @override
    async def get_homeworks(self, course: Course) -> list[Homework]:
        chapters = await self._get_chapter_info(course)
        schedules = await self._get_leaf_schedules(course)
        homeworks = []
        chapter_has_completed_video: dict[int, bool] = {}
        for leaf, chapter_name, section_name in iter_leaves_with_context(chapters):
            chapter_id_raw = leaf.get("chapter_id")
            chapter_id = int(chapter_id_raw) if chapter_id_raw is not None else -1
            if leaf.get("leaf_type") == 0:
                leaf_id = leaf.get("id")
                if leaf_id is not None and schedules.get(int(leaf_id), 0.0) >= 1.0:
                    chapter_has_completed_video[chapter_id] = True
                continue

            if leaf.get("leaf_type") != 6 or not leaf.get("is_show", True):
                continue
            if not chapter_has_completed_video.get(chapter_id, False):
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
                        "is_score": leaf.get("is_score"),
                        "is_assessed": leaf.get("is_assessed"),
                        "is_completed": is_completed,
                        "chapter_id": leaf.get("chapter_id"),
                        "chapter_name": chapter_name,
                        "section_name": section_name,
                        "start_time": leaf.get("start_time"),
                    },
                )
            )
        return homeworks

    @override
    async def get_leaf_questions(self, leaf_id: str | int, course: Course) -> list[dict[str, Any]]:
        # 1. 获取叶子节点信息以找到 leaf_type_id
        cid = course.metadata["classroom_id"]
        url = f"https://www.yuketang.cn/mooc-api/v1/lms/learn/leaf_info/{cid}/{leaf_id}/"
        kwargs = self._get_course_kwargs(course)
        resp_obj = await self.client.get(url, **kwargs)
        resp = resp_obj.json()
        leaf_type_id = resp["data"]["content_info"]["leaf_type_id"]

        # 2. 获取题目列表
        q_url = f"https://www.yuketang.cn/mooc-api/v1/lms/exercise/get_exercise_list/{leaf_type_id}/"
        q_resp_obj = await self.client.get(q_url, **kwargs)
        q_resp = q_resp_obj.json()
        return q_resp["data"]["problems"]

    @override
    async def do_video(self, video_id: str, video_name: str, course: Course) -> None:
        if not self.user:
            await self.login()

        assert self.user is not None
        user_id = int(self.user.id)

        if "free_sku_id" not in course.metadata:
            await self._get_chapter_info(course)

        cid = str(course.metadata["classroom_id"])
        course_id = course.metadata["course_id"]
        sku_id = course.metadata["free_sku_id"]

        vid_str = str(video_id)
        progress_url = f"https://www.yuketang.cn/video-log/get_video_watch_progress/?cid={course_id}&user_id={user_id}&classroom_id={cid}&video_type=video&vtype=rate&video_id={vid_str}&snapshot=1"
        heartbeat_url = "https://www.yuketang.cn/video-log/heartbeat/"

        def payload_gen(
            video_id,
            classroom_id,
            user_id,
            course_id,
            sku_id,
            video_frame,
            i,
            timestamp,
        ):
            return {
                "i": 5,
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
                "cc": video_id,
                "d": 4976.5,
                "pg": f"{video_id}_{''.join(random.sample('abcdefghijklmnopqrstuvwxyz0123456789', 4))}",
                "sq": i,
                "t": "video",
            }

        await generic_watch_video(
            self.client,
            vid_str,
            video_name,
            cid,
            user_id,
            course_id,
            sku_id,
            progress_url,
            heartbeat_url,
            payload_gen,
            request_kwargs=self._get_course_kwargs(course),
            on_progress=self.ui.update_video_progress,
            on_complete=self.ui.finish_video_progress,
            on_status=self.ui.update_video_status,
        )

    @override
    async def do_homework(self, homework: Homework, course: Course, is_random: bool = False) -> None:
        self.ui.update_homework_status(homework.name, "🧠 答题中")
        try:
            questions = await self.get_leaf_questions(homework.id, course)
            kwargs = self._get_course_kwargs(course)
            total_questions = len(questions)
            self.ui.update_homework_progress(homework.name, 0, total_questions)

            def _on_progress(done: int, total: int) -> None:
                self.ui.update_homework_progress(homework.name, done, total)

            async def submit_wrapper(
                problem_id: int,
                answer: list[str],
                course_info: Any,
                client: httpx.AsyncClient,
                kwargs: dict[str, Any] | None,
            ) -> dict[str, Any]:
                s_url = "https://www.yuketang.cn/mooc-api/v1/lms/exercise/problem_apply/"
                if isinstance(answer, str):
                    answer = [answer]
                payload = {
                    "classroom_id": course.metadata["classroom_id"],
                    "problem_id": problem_id,
                    "answer": answer,
                }

                while True:
                    resp_obj = await client.post(s_url, json=payload, **(kwargs or {}))
                    match = re.search(r"Expected available in(.+?)second.", resp_obj.text)
                    if match:
                        delay_time = float(match.group(1).strip())
                        remain = max(1, math.ceil(delay_time))
                        while remain > 0:
                            self.ui.update_homework_status(homework.name, f"⚠️ 限流，等待 {remain}s")
                            await asyncio.sleep(1)
                            remain -= 1
                        self.ui.update_homework_status(homework.name, "🔄 重试中...")
                        self.ui.update_homework_status(homework.name, "🧠 答题中")
                        continue
                    return resp_obj.json()

            if is_random:
                await generic_random_answer(
                    questions,
                    submit_wrapper,
                    None,
                    self.client,
                    headers=kwargs,
                    on_progress=_on_progress,
                )
            else:
                await generic_process_homework(
                    questions,
                    submit_wrapper,
                    None,
                    self.client,
                    headers=kwargs,
                    on_progress=_on_progress,
                )

            self.ui.update_homework_status(homework.name, "✅ 完成")
        except Exception:
            self.ui.update_homework_status(homework.name, "❌ 失败")
            raise
        finally:
            self.ui.finish_homework_progress(homework.name)
