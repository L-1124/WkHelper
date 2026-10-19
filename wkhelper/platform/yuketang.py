"""雨课堂平台实现。"""

import logging
from typing import Any, override

from wkhelper.core.base_platform import BasePlatform, HeartbeatProfile
from wkhelper.core.entities import Course, Homework, UserInfo, VideoContext
from wkhelper.core.exceptions import APIError, AuthError
from wkhelper.core.homework import is_fill_in_question
from wkhelper.core.qr_login import QrCodeSession

logger = logging.getLogger(__name__)


class YuketangPlatform(BasePlatform):
    """雨课堂平台。"""

    api_base = "https://www.yuketang.cn"
    api_prefix = "mooc-api"

    @property
    @override
    def heartbeat_profile(self) -> HeartbeatProfile:
        """雨课堂心跳报文的固定字段。"""
        return HeartbeatProfile(lob="ykt")

    @override
    async def _login_with_cookies(self, cookies: dict[str, str]) -> UserInfo:
        csrftoken = cookies.get("csrftoken", "")
        sessionid = cookies.get("sessionid", "")
        if not csrftoken or not sessionid:
            raise AuthError("Cookie 缺少 csrftoken 或 sessionid")

        self.client.headers.update(
            {
                "Content-Type": "application/json",
                "Referer": "https://www.yuketang.cn/",
                "X-CSRFToken": csrftoken,
                "xtbz": "ykt",
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

    qr_platform_name = "雨课堂"

    @override
    def _qr_request_payload(self) -> dict[str, Any]:
        return {
            "op": "requestlogin",
            "role": "web",
            "version": "1.4",
            "purpose": "login",
            "xtbz": "ykt",
            "x-client": "web",
        }

    @override
    async def _qr_publish_image(self, message: dict[str, Any], qr_session: QrCodeSession, expires_in: float) -> None:
        qrcode_data = message.get("qrcode")
        if qrcode_data:
            qr_session.create_image(qrcode_data)
            qr_session.emit_challenge(expires_in=expires_in)

    @override
    async def _qr_exchange_cookies(self, login_data: dict[str, Any]) -> dict[str, str]:
        if "Auth" not in login_data or "UserID" not in login_data:
            raise AuthError("扫码回执缺少 Auth 或 UserID")
        response = await self.client.post(
            f"{self.api_base}/pc/web_login",
            json={"Auth": login_data["Auth"], "UserID": int(login_data["UserID"])},
        )
        cookies = {
            "csrftoken": response.cookies.get("csrftoken") or "",
            "sessionid": response.cookies.get("sessionid") or "",
        }
        if not cookies["csrftoken"] or not cookies["sessionid"]:
            raise AuthError("登录凭据换取失败")
        return cookies

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
                "xtbz": "ykt",
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
        # 补充从 c27 接口获取作业与测验多类型叶子的真实完成状态
        try:
            p_url = f"https://www.yuketang.cn/c27/online_courseware/schedule/course/progress/?classroom_id={cid}"
            p_resp = await self._request_json("GET", p_url, **kwargs)
            cp = p_resp.get("data", {}).get("course_progress", [])

            def walk(nodes: list[dict[str, Any]]) -> None:
                for n in nodes:
                    if isinstance(n, dict):
                        lid = n.get("id")
                        prog = n.get("progress")
                        if lid is not None and prog is not None:
                            try:
                                schedules[int(lid)] = float(prog)
                            except (TypeError, ValueError):
                                pass
                        for k in ("section_leaf_list", "leaf_list", "section_list"):
                            children = n.get(k)
                            if isinstance(children, list):
                                walk(children)

            walk(cp)
        except Exception as exc:
            logger.debug(f"获取多类型任务进度失败: {exc}")

        return schedules

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
    def _get_leaf_info_params(self, leaf_id: str | int, course: Course) -> dict[str, Any]:
        return {"sign": course.metadata.get("sign", "")}

    @override
    def _build_exercise_list_url(self, leaf_type_id: int | str, sku_id: Any = None) -> str:
        if sku_id is not None:
            return f"{self.api_base}/{self.api_prefix}/v1/lms/exercise/get_exercise_list/{leaf_type_id}/{sku_id}/"
        return f"{self.api_base}/{self.api_prefix}/v1/lms/exercise/get_exercise_list/{leaf_type_id}/"

    @override
    def _get_request_kwargs(self, course: Course) -> dict[str, Any] | None:
        return self._get_course_kwargs(course)

    @override
    def _build_submit_payload(
        self,
        problem_id: int,
        answer: list[str],
        submit_ctx: dict[str, Any] | None = None,
        question: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cid = submit_ctx.get("classroom_id") if submit_ctx else None
        classroom_id = int(cid) if cid is not None else 0
        payload: dict[str, Any] = {
            "classroom_id": classroom_id,
            "problem_id": problem_id,
        }
        if is_fill_in_question(question):
            payload["answer"] = ""
            payload["answers"] = {str(i + 1): v for i, v in enumerate(answer)}
        else:
            payload["answer"] = answer
        return payload

    @override
    async def _prepare_video_context(self, video_id: str, course: Course) -> VideoContext:
        if not self.user:
            raise AuthError("未登录平台")

        user_id = int(self.user.id)
        if "free_sku_id" not in course.metadata or "course_id" not in course.metadata:
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

        media_ccid = ""
        duration = 0.0
        try:
            leaf_url = self._build_leaf_info_url(vid_str, course)
            leaf_resp = await self._request_json(
                "GET",
                leaf_url,
                params=self._get_leaf_info_params(vid_str, course),
                **kwargs,
            )
            content_info = leaf_resp.get("data", {}).get("content_info", {})
            if isinstance(content_info, dict):
                media = content_info.get("media")
                if isinstance(media, dict):
                    media_ccid = str(media.get("ccid") or "")
                    if media.get("duration") is not None:
                        try:
                            duration = float(media["duration"])
                        except (TypeError, ValueError):
                            duration = 0.0
        except Exception as exc:
            logger.debug(f"获取视频叶子详情失败: {exc}")

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
            media_ccid=media_ccid,
            duration=duration,
        )

    @override
    async def _prepare_submit_context(self, homework: Homework, course: Course) -> dict[str, Any]:
        return {
            "classroom_id": int(course.metadata["classroom_id"]),
        }

    @override
    async def _fetch_discussion_unit(self, leaf_id: str | int, course: Course) -> dict[str, Any]:
        cid = self._classroom_id(course)
        url = f"{self.api_base}/v/discussion/v2/unit/discussion/"
        kwargs = self._get_request_kwargs(course) or {}
        resp = await self._request_json(
            "GET",
            url,
            params={"classroom_id": cid, "leaf_id": str(leaf_id)},
            **kwargs,
        )
        data = resp.get("data")
        if not isinstance(data, dict) or "id" not in data:
            raise APIError(f"获取讨论题 {leaf_id} 详情失败")
        return data

    @override
    async def _discussion_completed(self, topic_info: dict[str, Any], course: Course, leaf_id: str | int) -> bool:
        if int(topic_info.get("user_comment_num") or 0) > 0:
            return True
        cid = self._classroom_id(course)
        url = f"{self.api_base}/v/discussion/v2/student/comment/status/"
        kwargs = self._get_request_kwargs(course) or {}
        resp = await self._request_json(
            "GET",
            url,
            params={"classroom_id": cid, "leaf_id": str(leaf_id)},
            **kwargs,
        )
        return bool(resp.get("data"))

    @override
    async def _post_discussion_comment(
        self,
        topic_info: dict[str, Any],
        content: str,
        course: Course,
        leaf_id: str | int,
    ) -> dict[str, Any]:
        url = f"{self.api_base}/v/discussion/v2/comment/"
        payload = {
            "to_user": int(topic_info.get("user_id") or 0),
            "topic_id": int(topic_info["id"]),
            "content": {
                "text": content,
                "app_upload_images": [],
            },
        }
        kwargs = self._get_request_kwargs(course) or {}
        return await self._request_json("POST", url, json=payload, **kwargs)
