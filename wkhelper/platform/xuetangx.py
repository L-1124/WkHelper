"""学堂在线平台实现。"""

import asyncio
import json
import logging
from typing import Any, override

import niquests
from terminal_qrcode import draw

from wkhelper.core.exceptions import APIError, AuthError
from wkhelper.core.models import Course, Homework, UserInfo, VideoContext
from wkhelper.platform.base import BasePlatform

logger = logging.getLogger(__name__)


class XuetangXPlatform(BasePlatform):
    """学堂在线平台。"""

    async def _login_with_cookies(self, cookies: dict[str, str]) -> UserInfo:
        csrftoken = cookies.get("csrftoken", "")
        sessionid = cookies.get("sessionid", "")
        if not csrftoken or not sessionid:
            raise AuthError("Cookie 缺少 csrftoken 或 sessionid")

        logger.info("🔐 正在验证 Cookie...")

        self.client.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/json",
                "X-CSRFToken": csrftoken,
                "Xtbz": "xt",
            }
        )
        self.client.cookies.update({"csrftoken": csrftoken, "sessionid": sessionid})

        resp = await self._request_json("GET", "https://www.xuetangx.com/api/v1/u/user/basic_profile/")
        if not resp["success"]:
            logger.error("❌ Cookie 验证失败，可能已过期")
            raise AuthError("Cookie 验证失败，请重新获取有效 Cookie")

        info = resp["data"]
        self.user = UserInfo(id=info["id"], name=info["name"], school=info.get("school"))
        self.current_cookies = cookies
        logger.info("✅ Cookie 登录成功！")
        return self.user

    async def _login_with_qrcode(self) -> UserInfo:
        logger.info("🔐 正在获取学堂在线 Cookie...")
        login_data: dict[str, Any] = {}

        request_payload = {
            "op": "requestlogin",
            "role": "web",
            "version": "1.4",
            "purpose": "login",
            "xtbz": "xt",
            "x-client": "web",
        }

        try:
            async with niquests.AsyncSession(timeout=10, verify=False) as ws_sess:
                resp = await ws_sess.get("wss://www.xuetangx.com/wsapp/")
                if resp.extension is None:
                    raise AuthError("WebSocket 升级失败")
                await resp.extension.send_payload(json.dumps(request_payload))
                timeout_seconds = 60.0

                while True:
                    try:
                        raw = await asyncio.wait_for(resp.extension.next_payload(), timeout=timeout_seconds)
                        if raw is None:
                            raise AuthError("WebSocket 连接已关闭")
                        message = json.loads(raw)

                        if "expire_seconds" in message:
                            timeout_seconds = float(message["expire_seconds"])

                        if "ticket" in message and message["ticket"]:
                            ticket_resp = await self.client.get(message["ticket"])
                            if ticket_resp.content:
                                print(draw(ticket_resp.content))
                            logger.info(f"请使用微信扫码登录 (有效时间: {int(timeout_seconds)}秒)...")

                        if message.get("op") == "loginsuccess":
                            login_data.update(message)
                            break

                    except TimeoutError:
                        logger.warning("⏳ 二维码已过期，正在重新请求...")
                        await resp.extension.send_payload(json.dumps(request_payload))

                await resp.extension.close()

        except Exception as e:
            logger.error(f"❌ WebSocket 连接失败: {e}")
            raise AuthError("WebSocket 连接失败") from e

        if "token" not in login_data:
            logger.error("❌ 登录失败，未获取到登录信息")
            raise AuthError("登录失败")

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
        return await self._login_with_cookies(cookies)

    @override
    async def get_courses(self) -> list[Course]:
        url = "https://www.xuetangx.com/api/v1/lms/user/user-courses/"
        resp = await self._request_json("GET", url, params={"status": "1", "page": "1"})
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

    # ── 抽象方法实现 ──

    @override
    async def _get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]
        url = "https://www.xuetangx.com/api/v1/lms/learn/course/chapter"
        resp = await self._request_json("GET", url, params={"cid": cid, "sign": sign})
        if not resp["success"]:
            raise APIError("获取章节信息失败")
        return resp["data"]["course_chapter"]

    @override
    async def _get_leaf_schedules(self, course: Course) -> dict[int, float]:
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]
        url = "https://www.xuetangx.com/api/v1/lms/learn/course/schedule"
        resp = await self._request_json("GET", url, params={"cid": cid, "sign": sign})
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
    def _build_leaf_info_url(self, leaf_id: str | int, course: Course) -> str:
        cid = course.metadata["classroom_id"]
        return f"https://www.xuetangx.com/api/v1/lms/learn/leaf_info/{cid}/{leaf_id}/"

    @override
    def _get_leaf_info_params(self, leaf_id: str | int, course: Course) -> dict[str, Any] | None:
        return {"sign": course.metadata["sign"]}

    @override
    def _build_exercise_list_url(self, leaf_type_id: int | str) -> str:
        return f"https://www.xuetangx.com/api/v1/lms/exercise/get_exercise_list/{leaf_type_id}/"

    @override
    def _build_submit_url(self) -> str:
        return "https://www.xuetangx.com/api/v1/lms/exercise/problem_apply/"

    @override
    def _build_submit_payload(self, problem_id: int, answer: list[str]) -> dict[str, Any]:
        ctx = self._submit_ctx
        return {
            "classroom_id": ctx["classroom_id"],
            "problem_id": problem_id,
            "leaf_id": ctx["leaf_id"],
            "exercise_id": ctx["exercise_id"],
            "sign": ctx["sign"],
            "answer": answer,
        }

    # ── 视频 ──

    @override
    async def _prepare_video_context(self, video_id: str, course: Course) -> VideoContext:
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]

        resp = await self._request_json(
            "GET",
            f"https://www.xuetangx.com/api/v1/lms/learn/leaf_info/{cid}/{video_id}/",
            params={"sign": sign},
        )
        data = resp["data"]

        user_id = int(data["user_id"])
        sku_id = int(data["sku_id"])
        course_id = int(data["course_id"])

        progress_params = {
            "cid": course_id,
            "user_id": user_id,
            "classroom_id": cid,
            "video_type": "video",
            "vtype": "rate",
            "video_id": video_id,
        }

        return VideoContext(
            video_id=str(video_id),
            classroom_id=str(cid),
            user_id=user_id,
            course_id=course_id,
            sku_id=sku_id,
            progress_url="https://www.xuetangx.com/video-log/get_video_watch_progress/",
            heartbeat_url="https://www.xuetangx.com/video-log/heartbeat/",
            progress_params=progress_params,
            request_kwargs={"params": progress_params},
        )

    # ── 作业 ──

    @override
    async def _prepare_submit_context(self, homework: Homework, course: Course) -> None:
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]

        # 需要单独获取 leaf_type_id 用于提交上下文
        url = self._build_leaf_info_url(homework.id, course)
        params = self._get_leaf_info_params(homework.id, course)
        resp = await self._request_json("GET", url, params=params)
        leaf_type_id = resp["data"]["content_info"]["leaf_type_id"]

        self._submit_ctx = {
            "leaf_id": homework.id,
            "exercise_id": leaf_type_id,
            "sign": sign,
            "classroom_id": cid,
        }
