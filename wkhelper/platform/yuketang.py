"""雨课堂平台实现。"""

import asyncio
import json
import logging
from typing import Any, override

import niquests

from wkhelper.core.config import DEFAULT_HEADERS
from wkhelper.core.exceptions import APIError, AuthError
from wkhelper.core.exporter import html_to_md
from wkhelper.core.models import Course, DiscussionCommentResult, DiscussionTopicDetail, Homework, UserInfo, VideoContext
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.login_helper import QrCodeSession

logger = logging.getLogger(__name__)


class YuketangPlatform(BasePlatform):
    """雨课堂平台。"""

    @override
    async def _login_with_cookies(self, cookies: dict[str, str]) -> UserInfo:
        csrftoken = cookies.get("csrftoken", "")
        sessionid = cookies.get("sessionid", "")
        if not csrftoken or not sessionid:
            raise AuthError("Cookie 缺少 csrftoken 或 sessionid")

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
            raise AuthError("Cookie 验证失败，可能已过期")

        info = data["data"]
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
            "version": 1.4,
            "type": "qrcode",
        }

        qr_session = QrCodeSession(
            display=display,
            open_browser=open_browser,
            platform_name="雨课堂",
        )
        await qr_session.start()

        try:
            async with niquests.AsyncSession(timeout=max(30.0, timeout + 15.0), verify=True) as ws_sess:
                resp = await ws_sess.get("wss://www.yuketang.cn/wsapp/")
                if resp.extension is None:
                    raise AuthError("WebSocket 升级失败")
                await resp.extension.send_payload(json.dumps(request_payload))

                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout

                while loop.time() < deadline:
                    remain_time = max(1.0, deadline - loop.time())
                    try:
                        raw = await asyncio.wait_for(resp.extension.next_payload(), timeout=min(60.0, remain_time))
                        if raw is None:
                            raise AuthError("WebSocket 连接已关闭")
                        message = json.loads(raw)

                        if "qrcode" in message and message["qrcode"]:
                            qr_session.create_image(message["qrcode"])
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

            if "Auth" not in login_data or "UserID" not in login_data:
                raise AuthError("扫码超时或未获得有效认证回执")

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
                    id=str(c["course"]["id"]),
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

    @override
    async def _get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
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
    async def _get_completed_video_ids(self, course: Course) -> set[int] | None:
        """用视频进度接口判断哪些视频已完成。"""
        if not self.user:
            raise AuthError("未登录平台")
        user_id = int(self.user.id)

        if "course_id" not in course.metadata:
            await self._get_chapter_data(course)

        kwargs = self._get_course_kwargs(course)
        try:
            resp = await self._request_json(
                "GET",
                "https://www.yuketang.cn/video-log/get_video_watched_record/",
                params={
                    "cid": course.metadata["course_id"],
                    "user_id": user_id,
                    "classroom_id": str(course.metadata["classroom_id"]),
                    "video_type": "video",
                    "term": "latest",
                    "uv_id": course.metadata["university_id"],
                },
                **kwargs,
            )
        except Exception as exc:
            logger.warning(f"获取视频完成记录失败，改按章节进度判断: {exc}")
            return None

        data = resp.get("data")
        if resp.get("code") != 0 or not isinstance(data, dict):
            logger.warning("获取视频完成记录异常，改按章节进度判断")
            return None

        completed: set[int] = set()
        for video_id, info in data.items():
            if not isinstance(info, dict):
                continue
            try:
                video_id_int = int(video_id)
            except (TypeError, ValueError):
                continue
            try:
                rate = float(info.get("rate") or 0.0)
            except (TypeError, ValueError):
                rate = 0.0
            # 以服务端判定 completed 为准；个别视频时长缺失时 rate 到不了 1.0，但 completed=1。
            if str(info.get("completed")) == "1" or rate >= 1.0:
                completed.add(video_id_int)
        return completed

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
    def _build_submit_payload(self, problem_id: int, answer: list[str], submit_ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        cid = submit_ctx.get("classroom_id") if submit_ctx else None
        return {
            "classroom_id": cid,
            "problem_id": problem_id,
            "answer": answer,
        }

    @override
    async def _prepare_video_context(self, video_id: str, course: Course) -> VideoContext:
        if not self.user:
            raise AuthError("未登录平台")

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

    @override
    async def _prepare_submit_context(self, homework: Homework, course: Course) -> dict[str, Any]:
        return {
            "classroom_id": course.metadata["classroom_id"],
        }

    @override
    async def _mark_article_finish(self, leaf_id: str | int, course: Course) -> bool:
        url = f"https://www.yuketang.cn/mooc-api/v1/lms/learn/user_article_finish/{leaf_id}/"
        kwargs = self._get_course_kwargs(course)
        resp = await self._request_json("GET", url, **kwargs)
        return bool(resp.get("success", False))

    @override
    async def get_discussion_detail(self, leaf_id: str | int, course: Course) -> DiscussionTopicDetail:
        cid = course.metadata["classroom_id"]
        kwargs = self._get_course_kwargs(course)
        url_unit = "https://www.yuketang.cn/v/discussion/v2/unit/discussion/"
        r_unit = await self._request_json("GET", url_unit, params={"classroom_id": cid, "leaf_id": leaf_id}, **kwargs)
        topic_info = r_unit.get("data", {})
        topic_id = int(topic_info.get("id") or 0)
        user_comment_num = int(topic_info.get("user_comment_num") or 0)
        comment_count = int(topic_info.get("commented") or 0)
        raw_text = topic_info.get("content", {}).get("text", "")

        url_status = "https://www.yuketang.cn/v/discussion/v2/student/comment/status/"
        r_status = await self._request_json("GET", url_status, params={"classroom_id": cid, "leaf_id": leaf_id}, **kwargs)
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
        kwargs = self._get_course_kwargs(course)
        url_unit = "https://www.yuketang.cn/v/discussion/v2/unit/discussion/"
        r_unit = await self._request_json("GET", url_unit, params={"classroom_id": cid, "leaf_id": leaf_id}, **kwargs)
        topic_info = r_unit.get("data", {})
        topic_id = int(topic_info.get("id") or 0)
        to_user = int(topic_info.get("user_id") or 0)

        url_post = "https://www.yuketang.cn/v/discussion/v2/comment/"
        payload = {
            "to_user": to_user,
            "topic_id": topic_id,
            "content": {
                "text": content,
                "app_upload_images": [],
            },
        }
        resp = await self.client.post(url_post, json=payload, **kwargs)
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
