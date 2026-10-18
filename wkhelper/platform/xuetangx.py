"""学堂在线平台实现。"""

import asyncio
import json
import logging
from typing import Any, override

import niquests

from wkhelper.core.exceptions import APIError, AuthError
from wkhelper.core.models import Course, Homework, UserInfo, VideoContext
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.login_helper import QrCodeSession

logger = logging.getLogger(__name__)


class XuetangXPlatform(BasePlatform):
    """学堂在线平台。"""

    @override
    async def _login_with_cookies(self, cookies: dict[str, str]) -> UserInfo:
        csrftoken = cookies.get("csrftoken", "")
        sessionid = cookies.get("sessionid", "")
        if not csrftoken or not sessionid:
            raise AuthError("Cookie 缺少 csrftoken 或 sessionid")

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
        if not resp.get("success"):
            raise AuthError("Cookie 验证失败，可能已过期")

        info = resp["data"]
        self.user = UserInfo(id=info["id"], name=info["name"], school=info.get("school"))
        self.current_cookies = cookies
        return self.user

    @override
    async def _login_with_qrcode(
        self,
        display: str = "image",
        open_browser: bool = False,
        timeout: int = 120,
    ) -> UserInfo:
        login_data: dict[str, Any] = {}
        request_payload = {
            "op": "requestlogin",
            "role": "web",
            "version": "1.4",
            "purpose": "login",
            "xtbz": "xt",
            "x-client": "web",
        }

        qr_session = QrCodeSession(display=display, open_browser=open_browser)
        await qr_session.start()

        try:
            async with niquests.AsyncSession(timeout=10, verify=True) as ws_sess:
                resp = await ws_sess.get("wss://www.xuetangx.com/wsapp/")
                if resp.extension is None:
                    raise AuthError("WebSocket 升级失败")
                await resp.extension.send_payload(json.dumps(request_payload))

                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout
                timeout_seconds = 60.0

                while loop.time() < deadline:
                    remain_time = max(1.0, deadline - loop.time())
                    try:
                        raw = await asyncio.wait_for(resp.extension.next_payload(), timeout=min(timeout_seconds, remain_time))
                        if raw is None:
                            raise AuthError("WebSocket 连接已关闭")
                        message = json.loads(raw)

                        if "expire_seconds" in message:
                            timeout_seconds = float(message["expire_seconds"])

                        if "ticket" in message and message["ticket"]:
                            ticket_resp = await self.client.get(message["ticket"])
                            if ticket_resp.content:
                                qr_session.create_image(ticket_resp.content)
                                qr_session.emit_challenge(expires_in=remain_time)

                        if message.get("op") == "loginsuccess":
                            login_data.update(message)
                            qr_session.mark_success()
                            break
                    except TimeoutError:
                        if loop.time() >= deadline:
                            break
                        await resp.extension.send_payload(json.dumps(request_payload))

                await resp.extension.close()

            if "token" not in login_data:
                raise AuthError("扫码超时或未获得有效认证回执")

            post_data = {"token": login_data["token"]}
            response = await self.client.post("https://www.xuetangx.com/account/wx/login/miniapp", json=post_data)
            cookies = {
                "csrftoken": response.cookies.get("csrftoken") or "",
                "sessionid": response.cookies.get("sessionid") or "",
            }

            if not cookies["csrftoken"] or not cookies["sessionid"]:
                raise AuthError("登录凭据换取失败")

            user = await self._login_with_cookies(cookies)
            qr_session.mark_success(name=user.name)
            if qr_session.display == "browser":
                await asyncio.sleep(1.2)
            return user
        except Exception as e:
            if isinstance(e, AuthError):
                raise
            raise AuthError(f"扫码通信失败: {e}") from e
        finally:
            await qr_session.close()

    @override
    async def get_courses(self) -> list[Course]:
        url = "https://www.xuetangx.com/api/v1/lms/user/user-courses/"
        resp = await self._request_json("GET", url, params={"status": "1", "page": "1"})
        if not resp.get("success"):
            raise APIError("获取课程列表失败")

        return [
            Course(
                id=str(c["course_id"]),
                name=c["name"],
                platform_id="xtzx",
                metadata={
                    "classroom_id": c["classroom_id"],
                    "sign": c["sign"],
                },
            )
            for c in resp["data"]["product_list"]
        ]

    @override
    async def _get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]
        url = f"https://www.xuetangx.com/api/v1/lms/learn/course/chapter?cid={cid}&sign={sign}"
        resp = await self._request_json("GET", url)
        return resp["data"]["course_chapter"]

    @override
    async def _get_leaf_schedules(self, course: Course) -> dict[int, float]:
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]
        url = f"https://www.xuetangx.com/api/v1/lms/learn/course/schedule?cid={cid}&sign={sign}"
        resp = await self._request_json("GET", url)
        if not resp.get("success"):
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
    def _build_submit_payload(self, problem_id: int, answer: list[str], submit_ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = submit_ctx or {}
        return {
            "classroom_id": ctx.get("classroom_id"),
            "problem_id": problem_id,
            "answer": answer,
            "exercise_id": ctx.get("exercise_id"),
            "leaf_id": ctx.get("leaf_id"),
            "sign": ctx.get("sign"),
        }

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

    @override
    async def _prepare_submit_context(self, homework: Homework, course: Course) -> dict[str, Any]:
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]

        url = self._build_leaf_info_url(homework.id, course)
        params = self._get_leaf_info_params(homework.id, course)
        resp = await self._request_json("GET", url, params=params)
        leaf_type_id = resp["data"]["content_info"]["leaf_type_id"]

        return {
            "leaf_id": homework.id,
            "exercise_id": leaf_type_id,
            "sign": sign,
            "classroom_id": cid,
        }
