"""学堂在线平台实现。"""

import asyncio
import json
import logging
from typing import Any, override

import niquests

from wkhelper.core.base_platform import BasePlatform, HeartbeatProfile
from wkhelper.core.entities import Course, Homework, UserInfo, VideoContext
from wkhelper.core.exceptions import APIError, AuthError
from wkhelper.core.exporter import html_to_md
from wkhelper.core.results import DiscussionCommentResult, DiscussionTopicDetail
from wkhelper.platform.login_helper import QrCodeSession

logger = logging.getLogger(__name__)


class XuetangXPlatform(BasePlatform):
    """学堂在线平台。"""

    @property
    @override
    def heartbeat_profile(self) -> HeartbeatProfile:
        """学堂在线心跳报文固定字段。

        现网沿用雨课堂报文格式（仅请求 URL 不同），因此此处保持与雨课堂一致的取值；
        若平台侧协议变化，只需在此覆写，不再改动 `core`。
        """
        return HeartbeatProfile(origin="ali-cdn.xuetangx.com", lob="ykt")

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

        qr_session = QrCodeSession(
            display=display,
            open_browser=open_browser,
            platform_name="学堂在线",
        )
        await qr_session.start()

        try:
            async with niquests.AsyncSession(timeout=max(30.0, timeout + 15.0), verify=True) as ws_sess:
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

            token_val = login_data.get("token") or login_data.get("s_s")
            if not token_val:
                raise AuthError("扫码超时或未获得有效认证回执")

            response = await self.client.post(
                "https://www.xuetangx.com/api/v1/u/login/wx/",
                json={"s_s": str(token_val)},
            )
            cookies = {
                "csrftoken": response.cookies.get("csrftoken") or self.client.cookies.get("csrftoken") or "",
                "sessionid": response.cookies.get("sessionid") or self.client.cookies.get("sessionid") or "",
            }

            if not cookies["csrftoken"] or not cookies["sessionid"]:
                raise AuthError(
                    f"登录凭据换取失败: status={response.status_code}, text={response.text[:200]}, "
                    f"cookies={dict(response.cookies)}, client_cookies={dict(self.client.cookies)}"
                )

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
                id=str(c.get("course_id") or c.get("product_id") or c.get("classroom_id")),
                name=c["name"],
                platform_id="xtzx",
                metadata={
                    "classroom_id": c["classroom_id"],
                    "sign": c["sign"],
                    "product_id": c.get("product_id"),
                    "sku_id": c.get("sku_id"),
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
        data = resp.get("data", {})
        leaf_type_id = data.get("content_info", {}).get("leaf_type_id") or data.get("leaf_type_id")

        return {
            "leaf_id": homework.id,
            "exercise_id": leaf_type_id,
            "sign": sign,
            "classroom_id": cid,
        }

    @override
    async def _mark_article_finish(self, leaf_id: str | int, course: Course) -> bool:
        cid = course.metadata["classroom_id"]
        sign = course.metadata["sign"]
        url = f"https://www.xuetangx.com/api/v1/lms/learn/user_article_finish/{leaf_id}/"
        resp = await self._request_json("GET", url, params={"sign": sign, "cid": cid})
        return bool(resp.get("success", False))

    @override
    async def get_discussion_detail(self, leaf_id: str | int, course: Course) -> DiscussionTopicDetail:
        cid = course.metadata["classroom_id"]
        url_unit = "https://www.xuetangx.com/v/discussion/v2/unit/discussion/"
        r_unit = await self._request_json("GET", url_unit, params={"classroom_id": cid, "leaf_id": leaf_id})
        topic_info = r_unit.get("data", {})
        topic_id = int(topic_info.get("id") or 0)
        user_comment_num = int(topic_info.get("user_comment_num") or 0)
        comment_count = int(topic_info.get("commented") or 0)
        raw_text = topic_info.get("content", {}).get("text", "")

        url_status = "https://www.xuetangx.com/v/discussion/v2/student/comment/status/"
        r_status = await self._request_json("GET", url_status, params={"classroom_id": cid, "leaf_id": leaf_id})
        completed = bool(r_status.get("data") or user_comment_num > 0)

        discussions = await self.get_discussions(course)
        target = next((item for item in discussions if item.id == str(leaf_id)), None)
        title = target.name if target else f"讨论题 {leaf_id}"

        return DiscussionTopicDetail(
            course_id=str(course.id),
            leaf_id=str(leaf_id),
            topic_id=topic_id,
            title=title,
            prompt=html_to_md(raw_text),
            comment_count=comment_count,
            user_comment_num=user_comment_num,
            completed=completed,
        )

    @override
    async def submit_discussion(self, leaf_id: str | int, course: Course, content: str) -> DiscussionCommentResult:
        cid = course.metadata["classroom_id"]
        url_unit = "https://www.xuetangx.com/v/discussion/v2/unit/discussion/"
        r_unit = await self._request_json("GET", url_unit, params={"classroom_id": cid, "leaf_id": leaf_id})
        topic_info = r_unit.get("data", {})
        topic_id = int(topic_info.get("id") or 0)
        to_user = int(topic_info.get("user_id") or 0)

        url_post = "https://www.xuetangx.com/v/discussion/v2/comment/"
        payload = {
            "to_user": to_user,
            "topic_id": topic_id,
            "content": {
                "text": content,
                "app_upload_images": [],
            },
        }
        resp = await self.client.post(url_post, json=payload)
        parsed = resp.json() if resp.text else {}
        success = resp.status_code == 200 and parsed.get("success", False)
        msg = parsed.get("msg") or parsed.get("data", {}).get("message") or "操作成功"
        comment_id = parsed.get("data", {}).get("data", {}).get("id")
        return DiscussionCommentResult(
            course_id=str(course.id),
            leaf_id=str(leaf_id),
            topic_id=topic_id,
            comment_id=int(comment_id) if comment_id else None,
            success=success,
            message=str(msg),
        )
