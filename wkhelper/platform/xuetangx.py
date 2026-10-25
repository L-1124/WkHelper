"""学堂在线平台实现。"""

import logging
from typing import Any, override
from urllib.parse import quote

from wkhelper.domain.accounts import UserInfo
from wkhelper.domain.content import NoticeDetail
from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError, AuthError
from wkhelper.domain.exam import ExamCoverDetail, ExamStartResult
from wkhelper.domain.homework import Homework
from wkhelper.domain.questions import is_fill_in_question, normalize_judgement_answer
from wkhelper.domain.time_types import epoch_millis
from wkhelper.domain.video import VideoContext
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.parsing import PlatformCounters, parse_leaf_schedules
from wkhelper.platform.protocol_types import HeartbeatProfile

logger = logging.getLogger(__name__)


class XuetangXPlatform(BasePlatform):
    def format_web_task_url(self, course: Course, leaf_id: str | int, task_type: str = "exercise") -> str | None:
        cid = self._classroom_id(course)
        lid = str(leaf_id)
        sign = str(course.metadata.get("sign") or "")
        return f"https://www.xuetangx.com/learn/{sign}/{course.id}/{cid}/{task_type}/{lid}"

    """学堂在线平台。"""

    api_base = "https://www.xuetangx.com"
    api_prefix = "api"
    cookie_domain = ".xuetangx.com"
    login_url = "https://www.xuetangx.com/account/login"
    essential_cookies = ("sessionid", "csrftoken")

    @property
    @override
    def heartbeat_profile(self) -> HeartbeatProfile:
        """学堂在线心跳报文固定字段。"""
        return HeartbeatProfile(lob="xt")

    @override
    async def login_with_cookies(self, cookies: dict[str, str]) -> UserInfo:
        csrftoken = cookies.get("csrftoken", "")
        sessionid = cookies.get("sessionid", "")
        if not csrftoken or not sessionid:
            raise AuthError("Cookie 缺少 csrftoken 或 sessionid")

        self.client.headers.update(
            {
                "Content-Type": "application/json",
                "X-CSRFToken": csrftoken,
                "app-name": "xtzx",
                "terminal-type": "web",
                "django-language": "zh",
                "Accept-Language": "zh",
                "xtbz": "xt",
                "x-client": "web",
            }
        )
        for name, value in (("csrftoken", csrftoken), ("sessionid", sessionid)):
            self.client.cookies.set(name, value, domain=self.cookie_domain)

        resp = await self._request_json("GET", "https://www.xuetangx.com/api/v1/u/user/basic_profile/")
        if not resp.get("success"):
            raise AuthError("Cookie 验证失败，可能已过期")

        info = resp["data"]
        display_name = info.get("name") or info.get("nickname") or str(info.get("id") or "")
        self.user = UserInfo(id=info["id"], name=display_name, school=info.get("school"))
        self.current_cookies = cookies
        return self.user

    @override
    async def get_courses(self) -> list[Course]:
        """分页拉取正在上课与已结课课程；接口返回 `pages` 字段，单页固定 20 条。"""
        url = f"{self.api_base}/api/v1/lms/user/user-courses/"
        courses: dict[str, Course] = {}
        # status：1=正在上课、3=已结课；2=即将开课无学习内容，4=已退课
        for status in ("1", "3"):
            page = 1
            while True:
                resp = await self._request_json("GET", url, params={"status": status, "page": str(page)})
                if not resp.get("success"):
                    raise APIError("获取课程列表失败")
                data = resp.get("data")
                if not isinstance(data, dict):
                    raise APIError("课程列表响应结构异常")
                product_list = data.get("product_list")
                if not isinstance(product_list, list):
                    raise APIError("课程列表响应缺少 product_list")
                for c in product_list:
                    if not isinstance(c, dict):
                        continue
                    course_id = str(c.get("course_id") or c.get("product_id") or c.get("classroom_id"))
                    # 同一 product_id 可能有多个学期课堂，去重键必须包含 classroom_id
                    dedupe_key = f"{course_id}:{c.get('classroom_id')}"
                    courses.setdefault(
                        dedupe_key,
                        Course(
                            id=course_id,
                            name=str(c.get("name") or "未命名课程"),
                            platform_id="xtzx",
                            metadata={
                                "classroom_id": c.get("classroom_id"),
                                "sign": c.get("sign"),
                                "product_id": c.get("product_id"),
                                "sku_id": c.get("sku_id"),
                            },
                        ),
                    )
                total_pages = PlatformCounters.model_validate(data).pages
                if page >= total_pages or not product_list:
                    break
                page += 1
        return list(courses.values())

    @override
    async def get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        resp = await self._request_json(
            "GET",
            f"{self.api_base}/api/v1/lms/learn/course/chapter",
            params={"cid": self._classroom_id(course), "sign": course.metadata["sign"]},
        )
        data = resp.get("data")
        chapters = data.get("course_chapter") if isinstance(data, dict) else None
        if not isinstance(chapters, list):
            raise APIError("章节大纲响应结构异常")
        return chapters

    @override
    async def get_leaf_schedules(self, course: Course) -> dict[int, float]:
        resp = await self._request_json(
            "GET",
            f"{self.api_base}/api/v1/lms/learn/course/schedule",
            params={"cid": self._classroom_id(course), "sign": course.metadata["sign"]},
        )
        if not resp.get("success"):
            return {}
        data = resp.get("data")
        return parse_leaf_schedules(data.get("leaf_schedules") if isinstance(data, dict) else None)

    @override
    def _get_leaf_info_params(self, leaf_id: str | int, course: Course) -> dict[str, Any] | None:
        return {"sign": course.metadata["sign"]}

    @override
    def _build_exercise_list_url(self, leaf_type_id: int | str, sku_id: Any = None) -> str:
        if sku_id is not None:
            return f"{self.api_base}/api/v1/lms/exercise/get_exercise_list/{leaf_type_id}/{sku_id}/"
        return f"{self.api_base}/api/v1/lms/exercise/get_exercise_list/{leaf_type_id}/"

    @override
    def _build_submit_payload(
        self,
        problem_id: int,
        answer: list[str],
        submit_ctx: dict[str, Any] | None = None,
        question: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = submit_ctx or {}
        answer = normalize_judgement_answer(question, answer)
        if is_fill_in_question(question):
            ans_val: Any = ""
            answers_val: dict[str, Any] = {str(i + 1): v for i, v in enumerate(answer)}
        else:
            ans_val = list(answer)
            answers_val = {}

        return {
            "classroom_id": ctx.get("classroom_id"),
            "problem_id": problem_id,
            "answer": ans_val,
            "answers": answers_val,
            "exercise_id": ctx.get("exercise_id"),
            "leaf_id": ctx.get("leaf_id"),
            "sign": ctx.get("sign"),
        }

    @override
    async def prepare_video_context(self, video_id: str, course: Course) -> VideoContext:
        cid = self._classroom_id(course)
        resp = await self._request_json(
            "GET",
            self._build_leaf_info_url(video_id, course),
            params=self._get_leaf_info_params(video_id, course),
        )
        data = resp.get("data")
        if not isinstance(data, dict) or "user_id" not in data:
            raise APIError(f"视频 {video_id} 上下文响应结构异常")

        user_id = int(data["user_id"])
        sku_id = int(data.get("sku_id") or 0)
        course_id = int(data.get("course_id") or 0)
        classroom_id = str(data.get("classroom_id") or cid)
        university_id = str(data.get("university_id") or course.metadata.get("university_id") or "")

        content_info = data.get("content_info")
        media = content_info.get("media") if isinstance(content_info, dict) and isinstance(content_info.get("media"), dict) else {}
        media_ccid = str(media.get("ccid") or media.get("ccurl") or "")
        duration = float(media.get("duration") or 0.0)

        progress_params = {
            "cid": course_id,
            "user_id": user_id,
            "classroom_id": classroom_id,
            "video_type": "video",
            "vtype": "rate",
            "video_id": str(video_id),
        }
        # 浏览器仅在解析出 uv 标识时才带这两个头，空值会形成异常指纹
        headers = {"classroom-id": classroom_id}
        if university_id and university_id != "0":
            headers["uv-id"] = university_id
            headers["university-id"] = university_id
        request_kwargs = {"headers": headers}

        return VideoContext(
            video_id=str(video_id),
            classroom_id=classroom_id,
            user_id=user_id,
            course_id=course_id,
            sku_id=sku_id,
            progress_url="https://www.xuetangx.com/video-log/get_video_watch_progress/",
            heartbeat_url="https://www.xuetangx.com/video-log/heartbeat/",
            progress_params=progress_params,
            request_kwargs=request_kwargs,
            media_ccid=media_ccid,
            duration=duration,
        )

    @override
    async def prepare_submit_context(self, homework: Homework, course: Course) -> dict[str, Any]:
        resp = await self._request_json(
            "GET",
            self._build_leaf_info_url(homework.id, course),
            params=self._get_leaf_info_params(homework.id, course),
        )
        data = resp.get("data")
        leaf_type_id = None
        if isinstance(data, dict):
            content_info = data.get("content_info")
            leaf_type_id = (content_info.get("leaf_type_id") if isinstance(content_info, dict) else None) or data.get("leaf_type_id")

        return {
            "leaf_id": homework.id,
            "exercise_id": leaf_type_id,
            "sign": course.metadata["sign"],
            "classroom_id": course.metadata["classroom_id"],
        }

    @override
    def _article_finish_params(self, course: Course) -> dict[str, Any] | None:
        return {"cid": self._classroom_id(course), "sid": course.metadata.get("sku_id")}

    @override
    async def fetch_discussion_unit(self, leaf_id: str | int, course: Course) -> dict[str, Any]:
        url = f"{self.api_base}/api/v1/lms/forum/unit/discussion/"
        params = {
            "product_sign": course.metadata.get("sign"),
            "leaf_id": leaf_id,
            "classroom_id": self._classroom_id(course),
            # 单元讨论固定 topic_type=4（1 为课程讨论区）
            "topic_type": 4,
            "channel": "xt",
        }
        resp = await self._request_json("GET", url, params=params)
        data = resp.get("data")
        if not isinstance(data, dict) or "id" not in data:
            raise APIError(f"讨论题单元 {leaf_id} 响应结构异常")
        return data

    @override
    async def discussion_completed(self, topic_info: dict[str, Any], course: Course, leaf_id: str | int) -> bool:
        return int(topic_info.get("user_comment_num") or 0) > 0

    @override
    async def discussion_leaf_completed(self, leaf_id: str | int, course: Course) -> bool | None:
        """学堂在线详情以本人评论数为准；列表按同一来源补查未完成项。"""
        unit = await self.fetch_discussion_unit(leaf_id, course)
        return int(unit.get("user_comment_num") or 0) > 0

    @override
    async def post_discussion_comment(
        self,
        topic_info: dict[str, Any],
        content: str,
        course: Course,
        leaf_id: str | int,
    ) -> dict[str, Any]:
        url = f"{self.api_base}/api/v1/lms/forum/comment/"
        params = {"classroom_id": self._classroom_id(course), "leaf_id": leaf_id}
        payload = {
            "to_user": int(topic_info.get("user_id") or 0),
            "topic_id": int(topic_info["id"]),
            "content": {
                "text": content,
                "upload_images": [],
            },
        }
        return await self._request_json("POST", url, params=params, json=payload)

    @override
    async def delete_discussion_comment(
        self,
        comment_id: str | int,
        course: Course,
    ) -> dict[str, Any]:
        url = f"{self.api_base}/api/v1/lms/forum/comment"
        params = {"id": str(comment_id), "cid": self._classroom_id(course)}
        return await self._request_json("DELETE", url, params=params)

    @override
    async def get_notice_detail(self, leaf_id: str | int, course: Course) -> NoticeDetail:
        """学堂在线前端暂无独立的公告正文接口，明确失败而不是返回空正文。"""
        raise APIError(f"学堂在线公告 {leaf_id} 暂不支持程序化读取，请在浏览器中查看", retryable=False)

    @override
    async def get_exam_cover(self, leaf_id: str | int, course: Course) -> ExamCoverDetail:
        url = self._build_leaf_info_url(leaf_id, course)
        params = self._get_leaf_info_params(leaf_id, course)
        resp = await self._request_json("GET", url, params=params)
        data = resp.get("data") or {}
        self._ensure_exam_leaf(leaf_id, data.get("leaf_type") if isinstance(data, dict) else None)
        ci = data.get("content_info") if isinstance(data, dict) else {}
        eid = (
            (ci.get("leaf_type_id") if isinstance(ci, dict) else None)
            or (data.get("leaf_type_id") if isinstance(data, dict) else None)
            or leaf_id
        )

        c_resp = await self._request_json("GET", f"{self.api_base}/api/v1/lms/exam/cover", params={"exam_id": eid})
        raw_cover = c_resp.get("data") or {}
        cover_data = raw_cover.get("data", raw_cover) if isinstance(raw_cover, dict) else {}

        raw_status = cover_data.get("result", {}).get("status") if isinstance(cover_data.get("result"), dict) else None
        if raw_status in (2, 3, 4, 5):
            status = "已交卷"
        elif raw_status == 1:
            status = "答题中"
        elif raw_status == 6:
            status = "已缺考"
        elif raw_status == 0:
            status = "未开始"
        else:
            status = str(raw_status) if raw_status is not None else "未知"

        deadline = epoch_millis(cover_data.get("deadline") or (data.get("deadline") if isinstance(data, dict) else None))
        if status in ("未开始", "答题中", "未知") and self._is_exam_overdue(deadline):
            status = "已缺考"

        face_status = cover_data.get("face_auth_status") if isinstance(cover_data.get("face_auth_status"), dict) else {}
        online_proctor = bool(cover_data.get("online_proctor") or face_status.get("online_proctor"))
        identity_auth = bool(cover_data.get("identity_auth") or face_status.get("has_idphoto"))
        face_auth = bool(identity_auth or online_proctor)
        capture_screen = bool(cover_data.get("capture_screen"))
        app_capture_screen = bool(cover_data.get("app_capture_screen"))
        front_random_snap = bool(cover_data.get("app_front_random_snap"))
        screen_cuts = bool(cover_data.get("open_screen_cuts"))
        max_screen_cuts = PlatformCounters.model_validate(cover_data).max_screen_cuts_num
        max_retry = PlatformCounters.model_validate(cover_data).max_retry
        is_offline = bool(cover_data.get("is_offline"))

        return ExamCoverDetail(
            course_id=str(course.id),
            exam_id=str(leaf_id),
            title=str(cover_data.get("title") or (data.get("name") if isinstance(data, dict) else None) or f"考试 {leaf_id}"),
            problem_count=int(cover_data.get("problem_count") or 0),
            total_score=float(cover_data.get("total_score") or 0.0),
            # 考场前端以 `1e3 * cover.limit` 换算毫秒展示，`limit` 单位为秒；0 表示不限时。
            limit_minutes=int(cover_data.get("limit") or 0) // 60,
            start_time=epoch_millis(cover_data.get("start_time") or (data.get("start_time") if isinstance(data, dict) else None)),
            deadline=deadline,
            status=status,
            description=str(cover_data.get("description") or ""),
            face_auth=face_auth,
            capture_screen=capture_screen,
            app_capture_screen=app_capture_screen,
            front_random_snap=front_random_snap,
            screen_cuts=screen_cuts,
            max_screen_cuts=max_screen_cuts,
            online_proctor=online_proctor,
            identity_auth=identity_auth,
            max_retry=max_retry,
            is_offline=is_offline,
            raw_status=raw_status if isinstance(raw_status, int) else None,
        )

    async def _mint_exam_room(self, leaf_id: str | int, course: Course, status: int | None = None) -> tuple[str, int, str]:
        """换取考场令牌并完成换票，返回 (exam_host, exam_id, 考场直达 URL)。"""
        url = self._build_leaf_info_url(leaf_id, course)
        params = self._get_leaf_info_params(leaf_id, course)
        resp = await self._request_json("GET", url, params=params)
        data = resp.get("data") or {}
        self._ensure_exam_leaf(leaf_id, data.get("leaf_type") if isinstance(data, dict) else None)
        ci = data.get("content_info") if isinstance(data, dict) else {}
        raw_eid = (
            (ci.get("leaf_type_id") if isinstance(ci, dict) else None)
            or (data.get("leaf_type_id") if isinstance(data, dict) else None)
            or leaf_id
        )
        eid = int(raw_eid)

        gen = await self._request_json("POST", f"{self.api_base}/api/v1/lms/exam/gen_token", json={"exam_id": eid})
        if gen.get("errcode") or gen.get("success") is False:
            reason = str(gen.get("errmsg") or gen.get("msg") or "平台未返回原因")
            raise APIError(f"考场令牌申请被拒绝: {reason}", retryable=False)
        gen_data = gen.get("data") if isinstance(gen.get("data"), dict) else {}
        token_data = gen_data.get("data") if isinstance(gen_data.get("data"), dict) else {}
        crypt = str(token_data.get("crypt") or token_data.get("token") or "")
        if not crypt:
            raise APIError(
                f"平台未返回考场令牌（节点 {leaf_id} 可能并非考试或当前账号无考试权限），已中止建立考场会话",
                retryable=False,
            )
        host = str(token_data.get("exam_host") or "https://examination.xuetangx.com").rstrip("/")
        user_id = str((self.user.id if self.user else None) or token_data.get("user_id") or "")
        target = "exam"
        if status is not None:
            target = "result" if status > 1 else ("start" if status == 0 else "exam")
        # 学堂在线 Web 换票固定 isFrom=0（雨课堂为 2）
        next_url = f"{host}/{target}/{eid}?isFrom=0"
        login_url = f"{host}/login?exam_id={eid}&user_id={user_id}&crypt={quote(crypt)}&next={quote(next_url, safe='')}&language=zh"
        login_resp = await self.client.get(login_url, allow_redirects=True)
        if login_resp.status_code >= 400:
            raise APIError(
                f"考场登录被拒绝（HTTP {login_resp.status_code}），令牌可能已失效或账号无权限",
                retryable=False,
            )
        return host, eid, login_url

    @override
    async def start_exam(self, leaf_id: str | int, course: Course) -> ExamStartResult:
        cover = await self.get_exam_cover(leaf_id, course)
        binding = getattr(self, "_exam_room_binding", None)
        if binding is not None:
            _host, _eid, login_url = binding
        else:
            _host, _eid, login_url = await self._mint_exam_room(leaf_id, course, cover.raw_status)
        return ExamStartResult(
            course_id=str(course.id),
            exam_id=str(leaf_id),
            title=cover.title,
            started=True,
            limit_minutes=cover.limit_minutes,
            web_url=login_url,
            message="考场会话已建立并完成令牌授权；如需启动服务端计时请改用 `exam start --begin`",
        )
