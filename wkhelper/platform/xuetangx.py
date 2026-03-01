"""学堂在线平台实现。"""

import asyncio
import logging
import math
import random
import re
from io import BytesIO
from typing import Any

import httpx
from httpx_ws import aconnect_ws

from wkhelper.core.exceptions import APIError, AuthError
from wkhelper.core.homework import generic_process_homework, generic_random_answer
from wkhelper.core.models import Course, Homework, UserInfo
from wkhelper.core.video import generic_watch_video
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.tree_utils import format_leaf_label, iter_leaves_with_context

logger = logging.getLogger(__name__)


class XuetangXPlatform(BasePlatform):
    """学堂在线平台。"""

    async def login(self) -> UserInfo:
        """扫码登录获取 Cookie。

        通过 WebSocket 获取登录二维码。若二维码过期，主动重新发送请求获取新码。

        Returns:
            UserInfo: 用户登录信息对象。

        Raises:
            AuthError: WebSocket 连接异常或最终未获取到 token 时抛出。
        """
        import qrcode
        from PIL import Image
        from pyzbar.pyzbar import decode

        logger.info("🔐 正在获取学堂在线 Cookie...")
        login_data = {}

        # 1. 提取请求参数，支持超时后复用发送
        request_payload = {
            "op": "requestlogin",
            "role": "web",
            "version": "1.4",
            "purpose": "login",
            "xtbz": "xt",
            "x-client": "web",
        }

        try:
            async with aconnect_ws("wss://www.xuetangx.com/wsapp/", self.client) as ws:
                # 首次请求二维码
                await ws.send_json(request_payload)

                # 设置默认超时变量，可被服务端的实际 expire_seconds 覆盖
                timeout_seconds = 60.0

                while True:
                    try:
                        # 2. 引入超时机制阻断无限期等待
                        message = await asyncio.wait_for(ws.receive_json(), timeout=timeout_seconds)

                        # 动态更新过期时间
                        if "expire_seconds" in message:
                            timeout_seconds = float(message["expire_seconds"])

                        if "ticket" in message and message["ticket"]:
                            # 下载并解析二维码
                            resp = await self.client.get(message["ticket"])
                            img = Image.open(BytesIO(resp.content))
                            decoded_objs = decode(img)
                            if decoded_objs:
                                url = decoded_objs[0].data.decode("utf-8")
                                qr = qrcode.QRCode()
                                qr.add_data(url)
                                qr.print_ascii(invert=True)
                                logger.info(f"请使用微信扫码登录 (有效时间: {int(timeout_seconds)}秒)...")

                        if message.get("op") == "loginsuccess":
                            login_data.update(message)
                            break

                    except TimeoutError:
                        # 3. 触发变量 A：过期后主动向服务端重新发送请求
                        logger.warning("⏳ 二维码已过期，正在重新请求...")
                        await ws.send_json(request_payload)

        except Exception as e:
            logger.error(f"❌ WebSocket 连接失败: {e}")
            raise AuthError("WebSocket 连接失败") from e

        if "token" not in login_data:
            logger.error("❌ 登录失败，未获取到登录信息")
            raise AuthError("登录失败")

        # 换取登录信息
        response = await self.client.post(
            "https://www.xuetangx.com/api/v1/u/login/wx/",
            json={
                "s_s": login_data["token"],
                "preset_properties": {
                    "$timezone_offset": -480,
                    "$screen_height": 1080,
                    "$screen_width": 1920,
                    "$lib": "js",
                    "$lib_version": "1.19.14",
                    "$latest_traffic_source_type": "直接流量",
                    "$latest_search_keyword": "未取到值_直接打开",
                    "$latest_referrer": "",
                    "$is_first_day": False,
                    "$referrer": "https://www.xuetangx.com/",
                    "$referrer_host": "www.xuetangx.com",
                    "$url": "https://www.xuetangx.com/",
                    "$url_path": "/",
                    "$title": "学堂在线 - 精品在线课程学习平台",
                    "_distinct_id": "auto-generated",
                },
                "page_name": "首页",
            },
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
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/json",
                "X-CSRFToken": cookies["csrftoken"],
                "Xtbz": "xt",
            }
        )
        self.client.cookies.update(cookies)

        # 获取基础信息
        resp_obj = await self.client.get("https://www.xuetangx.com/api/v1/u/user/basic_profile/")
        resp = resp_obj.json()
        if not resp["success"]:
            raise APIError("获取用户信息失败")

        info = resp["data"]
        self.user = UserInfo(id=info["id"], name=info["name"], school=info.get("school"))
        return self.user

    async def get_courses(self) -> list[Course]:
        url = "https://www.xuetangx.com/api/v1/lms/user/user-courses/?status=1&page=1"
        resp_obj = await self.client.get(url)
        resp = resp_obj.json()
        if not resp["success"]:
            raise APIError("获取课程列表失败")

        return [
            Course(
                id=course["classroom_id"],
                name=course["name"],
                platform_id="xtzx",
                metadata={
                    "classroom_id": course["classroom_id"],
                    "sign": course["sign"],
                    "product_id": course["product_id"],
                    "sku_id": course["sku_id"],
                },
            )
            for course in resp["data"]["product_list"]
        ]

    async def _get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]
        url = f"https://www.xuetangx.com/api/v1/lms/learn/course/chapter?cid={cid}&sign={sign}"
        resp_obj = await self.client.get(url)
        resp = resp_obj.json()
        if not resp["success"]:
            raise APIError("获取章节信息失败")
        return resp["data"]["course_chapter"]

    async def _get_leaf_schedules(self, course: Course) -> dict[int, float]:
        """获取课程叶子节点进度。"""
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]
        url = f"https://www.xuetangx.com/api/v1/lms/learn/course/schedule?cid={cid}&sign={sign}"
        resp_obj = await self.client.get(url)
        resp = resp_obj.json()
        if not resp.get("success"):
            logger.warning("⚠️ 获取课程进度失败，默认按未完成处理")
            return {}

        raw = resp.get("data", {}).get("leaf_schedules", {})
        schedules: dict[int, float] = {}
        for leaf_id, progress in raw.items():
            try:
                schedules[int(leaf_id)] = float(progress)
            except TypeError, ValueError:
                continue
        return schedules

    async def get_videos(self, course: Course) -> dict[int, str]:
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

    async def get_homeworks(self, course: Course) -> list[Homework]:
        data = await self._get_chapter_data(course)
        schedules = await self._get_leaf_schedules(course)
        homeworks = []
        chapter_has_completed_video: dict[int, bool] = {}
        for leaf, chapter_name, section_name in iter_leaves_with_context(data):
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
        # 1. 获取叶子节点信息以找到 leaf_type_id
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]
        url = f"https://www.xuetangx.com/api/v1/lms/learn/leaf_info/{cid}/{leaf_id}/?sign={sign}"
        resp_obj = await self.client.get(url)
        resp = resp_obj.json()
        leaf_type_id = resp["data"]["content_info"]["leaf_type_id"]

        # 2. 获取题目列表
        q_url = f"https://www.xuetangx.com/api/v1/lms/exercise/get_exercise_list/{leaf_type_id}/"
        q_resp_obj = await self.client.get(q_url)
        q_resp = q_resp_obj.json()
        return q_resp["data"]["problems"]

    async def do_video(self, video_id: str, video_name: str, course: Course) -> None:
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]

        # 获取视频详情
        resp_obj = await self.client.get(f"https://www.xuetangx.com/api/v1/lms/learn/leaf_info/{cid}/{video_id}/?sign={sign}")
        resp = resp_obj.json()
        data = resp["data"]

        user_id = data["user_id"]
        sku_id = data["sku_id"]
        course_id = data["course_id"]

        progress_url = f"https://www.xuetangx.com/video-log/get_video_watch_progress/?cid={course_id}&user_id={user_id}&classroom_id={cid}&video_type=video&vtype=rate&video_id={video_id}"
        heartbeat_url = "https://www.xuetangx.com/video-log/heartbeat/"

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
                "skuid": sku_id,
                "classroomid": str(classroom_id),
                "cc": video_id,
                "d": 4976.5,
                "pg": f"{video_id}_{''.join(random.sample('abcdefghijklmnopqrstuvwxyz0123456789', 4))}",
                "sq": i,
                "t": "video",
            }

        await generic_watch_video(
            self.client,
            str(video_id),
            video_name,
            str(cid),
            int(user_id),
            int(course_id),
            int(sku_id),
            progress_url,
            heartbeat_url,
            payload_gen,
            on_progress=self.ui.update_video_progress,
            on_complete=self.ui.finish_video_progress,
            on_status=self.ui.update_video_status,
        )

    async def do_homework(self, homework: Homework, course: Course, is_random: bool = False) -> None:
        self.ui.update_homework_status(homework.name, "🧠 答题中")
        try:
            # 1. 获取叶子节点信息以找到 leaf_type_id
            cid = course.metadata["classroom_id"]
            sign = course.metadata["sign"]
            url = f"https://www.xuetangx.com/api/v1/lms/learn/leaf_info/{cid}/{homework.id}/?sign={sign}"
            resp_obj = await self.client.get(url)
            resp = resp_obj.json()
            leaf_type_id = resp["data"]["content_info"]["leaf_type_id"]

            # 2. 获取题目列表
            q_url = f"https://www.xuetangx.com/api/v1/lms/exercise/get_exercise_list/{leaf_type_id}/"
            q_resp_obj = await self.client.get(q_url)
            q_resp = q_resp_obj.json()
            questions = q_resp["data"]["problems"]
            total_questions = len(questions)
            self.ui.update_homework_progress(homework.name, 0, total_questions)

            # 3. 提交时的上下文信息
            ctx = {
                "leaf_id": homework.id,
                "exercise_id": leaf_type_id,
                "sign": sign,
                "classroom_id": cid,
            }

            def _on_progress(done: int, total: int) -> None:
                self.ui.update_homework_progress(homework.name, done, total)

            async def submit_wrapper(
                problem_id: int,
                answer: list[str],
                course_info: Any,
                client: httpx.AsyncClient,
                kwargs: dict[str, Any] | None,
            ) -> dict[str, Any]:
                url_apply = "https://www.xuetangx.com/api/v1/lms/exercise/problem_apply/"
                if isinstance(answer, str):
                    answer = [answer]

                payload = {
                    "classroom_id": course_info["classroom_id"],
                    "problem_id": problem_id,
                    "leaf_id": course_info["leaf_id"],
                    "exercise_id": course_info["exercise_id"],
                    "sign": course_info["sign"],
                    "answer": answer,
                }

                while True:
                    response = await client.post(url_apply, json=payload)
                    match = re.search(r"Expected available in(.+?)second.", response.text)
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

                    data = response.json()
                    if data.get("success") is True:
                        result_data = data.get("data", {})
                        await asyncio.sleep(random.uniform(3, 4))
                        return {
                            "success": True,
                            "is_correct": result_data.get("is_right", result_data.get("is_correct", False)),
                            "correct_answer": result_data.get("answer", []),
                        }
                    return {"success": False, "is_correct": False, "correct_answer": []}

            if is_random:
                await generic_random_answer(
                    questions,
                    submit_wrapper,
                    ctx,
                    self.client,
                    on_progress=_on_progress,
                )
            else:
                await generic_process_homework(
                    questions,
                    submit_wrapper,
                    ctx,
                    self.client,
                    on_progress=_on_progress,
                )

            self.ui.update_homework_status(homework.name, "✅ 完成")
        except Exception:
            self.ui.update_homework_status(homework.name, "❌ 失败")
            raise
        finally:
            self.ui.finish_homework_progress(homework.name)
