"""雨课堂平台实现。"""

import asyncio
import json
import logging
from typing import Any, Literal, cast, override

import niquests
import qrcode
from terminal_qrcode import draw

from wkhelper.core.config import DEFAULT_HEADERS
from wkhelper.core.exceptions import APIError, AuthError
from wkhelper.core.models import Course, Homework, UserInfo, VideoContext
from wkhelper.platform.base import BasePlatform

logger = logging.getLogger(__name__)


def render_login_qrcode(
    payload: str,
    *,
    renderer: Literal["auto", "halfblock", "iterm2", "kitty", "sixel", "wezterm"] = "auto",
) -> str:
    """将登录字符串渲染为终端二维码。"""
    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    return str(draw(cast(Any, qr.get_matrix()), renderer=renderer))


class YuketangPlatform(BasePlatform):
    """雨课堂平台。"""

    async def _login_with_cookies(self, cookies: dict[str, str]) -> UserInfo:
        csrftoken = cookies.get("csrftoken", "")
        sessionid = cookies.get("sessionid", "")
        if not csrftoken or not sessionid:
            raise AuthError("Cookie 缺少 csrftoken 或 sessionid")

        logger.info("🔐 正在验证 Cookie...")

        self.client.headers.update(
            {
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
                "Content-Type": "application/json",
                "Referer": "https://www.yuketang.cn/",
                "X-CSRFToken": csrftoken,
                "Xtbz": "ykt",
            }
        )
        self.client.cookies.update({"csrftoken": csrftoken, "sessionid": sessionid})

        data = await self._request_json("GET", "https://www.yuketang.cn/api/v3/user/basic-info")
        if data["code"] != 0:
            logger.error("❌ Cookie 验证失败，可能已过期")
            raise AuthError("Cookie 验证失败，请重新获取有效 Cookie")

        info = data["data"]
        self.user = UserInfo(id=info["id"], name=info["name"], school=info.get("school"))
        self.current_cookies = cookies
        logger.info("✅ Cookie 登录成功！")
        return self.user

    async def _login_with_qrcode(self) -> UserInfo:
        logger.info("🔐 正在获取雨课堂 Cookie...")
        login_data: dict[str, Any] = {}

        request_payload = {
            "op": "requestlogin",
            "role": "web",
            "version": 1.4,
            "type": "qrcode",
        }

        try:
            async with niquests.AsyncSession(timeout=10, verify=False) as ws_sess:
                resp = await ws_sess.get("wss://www.yuketang.cn/wsapp/")
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

                        if "qrcode" in message and message["qrcode"]:
                            print(render_login_qrcode(message["qrcode"], renderer="halfblock"))
                            logger.info(f"请使用雨课堂扫码登录 (判定有效时间: {int(timeout_seconds)}秒)...")

                        if message.get("op") == "loginsuccess":
                            login_data.update(message)
                            break

                    except TimeoutError:
                        logger.warning("⏳ 二维码可能已过期，正在重新请求...")
                        await resp.extension.send_payload(json.dumps(request_payload))

                await resp.extension.close()

        except Exception as e:
            logger.error(f"❌ WebSocket 连接失败: {e}")
            raise AuthError("WebSocket 连接失败") from e

        if "Auth" not in login_data or "UserID" not in login_data:
            logger.error("❌ 登录失败，未获取到登录信息")
            raise AuthError("登录失败")

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
        return await self._login_with_cookies(cookies)

    @override
    async def get_courses(self) -> list[Course]:
        url = "https://www.yuketang.cn/v2/api/web/courses/list"
        resp = await self._request_json("GET", url, params={"identity": "2"})
        if resp["errcode"] != 0:
            raise APIError("获取课程列表失败")

        courses = []
        for c in resp["data"]["list"]:
            teacher_name = c.get("teacher", {}).get("name")
            class_name = c.get("name")
            display_name = c["course"]["name"]
            if teacher_name:
                display_name += f" ({teacher_name})"
            if class_name:
                display_name += f" - {class_name}"
            courses.append(
                Course(
                    id=c["course"]["id"],
                    name=display_name,
                    platform_id="ykt",
                    metadata={
                        "classroom_id": c["classroom_id"],
                        "university_id": c["course"]["university_id"],
                    },
                )
            )
        return courses

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
        url = f"https://www.yuketang.cn/v2/api/web/classrooms/{cid}"
        kwargs = self._get_course_kwargs(course)
        resp = await self._request_json("GET", url, params={"role": "5"}, **kwargs)
        if resp["errcode"] != 0:
            raise APIError("获取课堂信息失败")
        return resp["data"]

    # ── 抽象方法实现 ──

    @override
    async def _get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        """获取章节树数据（包含 side-effect: 设置 course.metadata 的 sign/sku/course_id）。"""
        info = await self._get_classroom_info(course)
        course.metadata["sign"] = info.get("course_sign", "")
        course.metadata["free_sku_id"] = info.get("free_sku_id")
        course.metadata["course_id"] = info.get("course_id")

        cid = course.metadata["classroom_id"]
        uid = course.metadata["university_id"]
        sign = course.metadata["sign"]

        url = "https://www.yuketang.cn/mooc-api/v1/lms/learn/course/chapter"
        kwargs = self._get_course_kwargs(course)
        resp = await self._request_json(
            "GET",
            url,
            params={
                "cid": cid,
                "sign": sign,
                "term": "latest",
                "uv_id": uid,
                "classroom_id": cid,
            },
            **kwargs,
        )
        return resp["data"]["course_chapter"]

    @override
    async def _get_leaf_schedules(self, course: Course) -> dict[int, float]:
        cid = course.metadata["classroom_id"]
        uid = course.metadata["university_id"]
        sign = course.metadata.get("sign", "")
        kwargs = self._get_course_kwargs(course)
        url = "https://www.yuketang.cn/mooc-api/v1/lms/learn/course/schedule"
        resp = await self._request_json(
            "GET",
            url,
            params={
                "cid": cid,
                "sign": sign,
                "term": "latest",
                "uv_id": uid,
                "classroom_id": cid,
            },
            **kwargs,
        )
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
        return f"https://www.yuketang.cn/mooc-api/v1/lms/learn/leaf_info/{cid}/{leaf_id}/"

    @override
    def _build_exercise_list_url(self, leaf_type_id: int | str) -> str:
        return f"https://www.yuketang.cn/mooc-api/v1/lms/exercise/get_exercise_list/{leaf_type_id}/"

    @override
    def _get_request_kwargs(self, course: Course) -> dict[str, Any] | None:
        return self._get_course_kwargs(course)

    @override
    def _build_submit_url(self) -> str:
        return "https://www.yuketang.cn/mooc-api/v1/lms/exercise/problem_apply/"

    @override
    def _build_submit_payload(self, problem_id: int, answer: list[str]) -> dict[str, Any]:
        return {
            "classroom_id": self._submit_ctx["classroom_id"],
            "problem_id": problem_id,
            "answer": answer,
        }

    # ── 视频 ──

    @override
    async def _prepare_video_context(self, video_id: str, course: Course) -> VideoContext:
        if not self.user:
            await self.login()

        assert self.user is not None
        user_id = int(self.user.id)

        if "free_sku_id" not in course.metadata:
            await self._get_chapter_data(course)

        cid = str(course.metadata["classroom_id"])
        course_id = course.metadata["course_id"]
        sku_id = course.metadata["free_sku_id"]

        vid_str = str(video_id)
        progress_params = {
            "cid": course_id,
            "user_id": user_id,
            "classroom_id": cid,
            "video_type": "video",
            "vtype": "rate",
            "video_id": vid_str,
            "snapshot": "1",
        }
        kwargs = self._get_course_kwargs(course)
        kwargs.setdefault("params", {}).update(progress_params)

        return VideoContext(
            video_id=vid_str,
            classroom_id=cid,
            user_id=user_id,
            course_id=course_id,
            sku_id=sku_id,
            progress_url="https://www.yuketang.cn/video-log/get_video_watch_progress/",
            heartbeat_url="https://www.yuketang.cn/video-log/heartbeat/",
            progress_params=progress_params,
            request_kwargs=kwargs,
        )

    # ── 作业 ──

    @override
    async def _prepare_submit_context(self, homework: Homework, course: Course) -> None:
        self._submit_ctx = {
            "classroom_id": course.metadata["classroom_id"],
        }
