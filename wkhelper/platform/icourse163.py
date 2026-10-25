"""中国大学MOOC (icourse163.org) 平台适配实现。"""

import logging
from typing import Any, override

from wkhelper.domain.accounts import UserInfo
from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError, AuthError
from wkhelper.domain.homework import Homework
from wkhelper.domain.video import VideoContext
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.protocol_types import HeartbeatProfile

logger = logging.getLogger(__name__)

_CONTENT_TYPE_TO_LEAF_TYPE: dict[int, int] = {
    1: 0,
    3: 7,
    4: 3,
    5: 9,
    6: 4,
}


def _unit_leaf(unit: dict[str, Any]) -> dict[str, Any]:
    content_type = int(unit.get("contentType") or 0)
    return {
        "id": unit.get("id"),
        "name": unit.get("name") or "未命名任务",
        "leaf_type": _CONTENT_TYPE_TO_LEAF_TYPE.get(content_type, -1),
        "content_id": unit.get("contentId"),
        "content_type": content_type,
        "duration": unit.get("durationInSeconds"),
        "is_show": unit.get("visible") != 0,
    }


def _assessment_leaf(lesson: dict[str, Any], leaf_type: int) -> dict[str, Any]:
    assessment = lesson.get("test") or lesson.get("exam") or lesson
    return {
        "id": lesson.get("contentId") or assessment.get("id") or lesson.get("id"),
        "name": lesson.get("name") or assessment.get("name") or "未命名评测",
        "leaf_type": leaf_type,
        "content_id": lesson.get("contentId") or assessment.get("id"),
        "start_time": assessment.get("releaseTime") or lesson.get("releaseTime"),
        "score_deadline": assessment.get("deadline"),
        "is_show": lesson.get("visible") != 0,
    }


class Icourse163Platform(BasePlatform):
    """中国大学MOOC (icourse163.org) 平台。"""

    api_base = "https://www.icourse163.org"
    cookie_domain = ".icourse163.org"
    impersonate = "chrome"
    login_url = "https://www.icourse163.org/member/login.htm"
    essential_cookies = ("STUDY_SESS", "NTESSTUDYSI")

    @property
    @override
    def heartbeat_profile(self) -> HeartbeatProfile:
        return HeartbeatProfile(lob="mooc")

    def _get_csrf_key(self) -> str:
        """从已保存或当前客户端 Cookie 中提取 csrfKey。"""
        if self.current_cookies:
            for key in ("NTESSTUDYSI", "csrfKey"):
                val = self.current_cookies.get(key)
                if val:
                    return val
        for key in ("NTESSTUDYSI", "csrfKey"):
            val = self.client.cookies.get(key)
            if val:
                return val
        return ""

    async def _rpc_post(self, bean_and_method: str, data: dict[str, Any]) -> Any:
        """调用中国大学MOOC通用 Web RPC 接口。"""
        csrf = self._get_csrf_key()
        url = f"{self.api_base}/web/j/{bean_and_method}.rpc"
        if csrf:
            url = f"{url}?csrfKey={csrf}"

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": self.api_base,
            "Referer": f"{self.api_base}/",
        }
        resp = await self._request_json("POST", url, data=data, headers=headers)
        code = resp.get("code")
        if code != 0:
            msg = str(resp.get("message") or "")
            if code == 1 and ("not_auth" in msg or "login" in msg.lower()):
                raise AuthError("中国大学MOOC 登录凭据无效或已过期")
            raise APIError(f"中国大学MOOC 接口调用失败 [{code}]: {msg or '未知错误'}")
        return resp.get("result")

    @override
    async def login_with_cookies(self, cookies: dict[str, str]) -> UserInfo:
        for name, value in cookies.items():
            self.client.cookies.set(name, value, domain=self.cookie_domain)

        self.client.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Origin": self.api_base,
                "Referer": f"{self.api_base}/",
            }
        )
        self.current_cookies = dict(cookies)

        # 首页按 Accept 返回 JSON 页面模型，其中 webUser 来自服务端认证会话。
        payload = await self._request_json("GET", f"{self.api_base}/", headers={"Accept": "application/json, text/plain, */*"})
        member = payload.get("webUser")
        if not isinstance(member, dict) or not member.get("id"):
            raise AuthError("中国大学MOOC 登录凭据无效或已过期")

        user_id = str(member["id"])
        display_name = member.get("realName") or member.get("nickName") or user_id
        school = member.get("schoolName")

        self.user = UserInfo(id=user_id, name=display_name, school=school)
        return self.user

    @override
    async def get_courses(self) -> list[Course]:
        """分页拉取用户已选修的所有课程。"""
        courses: dict[str, Course] = {}
        page_index = 1
        page_size = 50

        while True:
            result = await self._rpc_post(
                "learnerCourseRpcBean.getMyLearnedCoursePanelList",
                {"type": 30, "p": page_index, "psize": page_size, "courseType": 1},
            )
            if not isinstance(result, dict):
                break
            items = result.get("result")
            if not isinstance(items, list) or not items:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                term = item.get("termPanel") or {}
                school = item.get("schoolPanel") or {}
                term_id = str(term.get("id") or "")
                course_id = str(item.get("id") or "")
                primary_id = term_id or course_id
                course_name = str(item.get("name") or "未命名课程")
                school_name = school.get("name")

                courses.setdefault(
                    primary_id,
                    Course(
                        id=primary_id,
                        name=course_name,
                        platform_id="icourse163",
                        metadata={
                            "term_id": term_id,
                            "course_id": course_id,
                            "school_name": school_name,
                            "term_name": term.get("courseName"),
                            "img_url": item.get("imgUrl"),
                        },
                    ),
                )

            query_meta = result.get("pagination")
            totle_pages = (
                int(query_meta.get("totlePageCount")) if isinstance(query_meta, dict) and query_meta.get("totlePageCount") else None
            )
            if totle_pages is not None:
                if page_index >= totle_pages:
                    break
            elif len(items) < page_size:
                break
            page_index += 1

        return list(courses.values())

    @override
    async def get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        """获取课程章节结构并将其归一化为统一的节点树。"""
        term_id = course.metadata.get("term_id") or course.id
        result = await self._rpc_post(
            "courseBean.getMocTermDto",
            {"termId": term_id, "isDraft": 0, "excludeUnrelease": 1},
        )
        if not isinstance(result, dict) or "chapters" not in result:
            result = await self._rpc_post("courseBean.getLastLearnedMocTermDto", {"termId": term_id})
            if isinstance(result, dict):
                result = result.get("mocTermDto")

        if not isinstance(result, dict):
            return []

        raw_chapters = result.get("chapters")
        if not isinstance(raw_chapters, list):
            return []

        normalized_chapters: list[dict[str, Any]] = []
        for ch in raw_chapters:
            if not isinstance(ch, dict):
                continue
            ch_item: dict[str, Any] = {
                "id": ch.get("id"),
                "name": ch.get("name") or "未命名章节",
                "section_list": [],
            }
            raw_lessons = ch.get("lessons") or []
            for lesson in raw_lessons:
                if not isinstance(lesson, dict):
                    continue
                lesson_item: dict[str, Any] = {
                    "id": lesson.get("id"),
                    "name": lesson.get("name") or "未命名小节",
                    "leaf_list": [],
                }
                lesson_item["leaf_list"] = [_unit_leaf(unit) for unit in lesson.get("units") or [] if isinstance(unit, dict)]
                if lesson.get("test"):
                    lesson_item["leaf_list"].append(_assessment_leaf(lesson, 6))
                if lesson.get("exam"):
                    lesson_item["leaf_list"].append(_assessment_leaf(lesson, 5))
                ch_item["section_list"].append(lesson_item)
            for group in ("homeworks", "quizs"):
                for lesson in ch.get(group) or []:
                    if not isinstance(lesson, dict):
                        continue
                    ch_item["section_list"].append(
                        {
                            "id": lesson.get("id"),
                            "name": lesson.get("name") or "章节测验",
                            "leaf_list": [_assessment_leaf(lesson, 6)],
                        }
                    )
            if isinstance(ch.get("exam"), dict):
                exam = ch["exam"]
                ch_item["section_list"].append(
                    {"id": exam.get("id"), "name": exam.get("name") or "考试", "leaf_list": [_assessment_leaf(exam, 5)]}
                )
            normalized_chapters.append(ch_item)

        return normalized_chapters

    @override
    async def get_leaf_schedules(self, course: Course) -> dict[int, float]:
        """获取各节点的学习进度。"""
        term_id = course.metadata.get("term_id") or course.id
        result = await self._rpc_post("courseBean.getLastLearnedMocTermDto", {"termId": term_id})
        if not isinstance(result, dict) or not isinstance(result.get("mocTermDto"), dict):
            return {}

        schedules: dict[int, float] = {}
        chapters = result["mocTermDto"].get("chapters")
        if isinstance(chapters, list):
            for ch in chapters:
                if not isinstance(ch, dict):
                    continue
                for lesson in ch.get("lessons") or []:
                    if not isinstance(lesson, dict):
                        continue
                    for unit in lesson.get("units") or []:
                        if not isinstance(unit, dict):
                            continue
                        uid = unit.get("id")
                        if uid is None:
                            continue
                        percent = unit.get("completePercent")
                        if percent is not None:
                            try:
                                schedules[int(uid)] = float(percent)
                            except (TypeError, ValueError):
                                continue
        return schedules

    @override
    async def prepare_video_context(self, video_id: str, course: Course) -> VideoContext:
        raise NotImplementedError("中国大学MOOC视频学习上下文尚未接入")

    @override
    async def prepare_submit_context(self, homework: Homework, course: Course) -> dict[str, Any]:
        raise NotImplementedError("中国大学MOOC作业提交上下文尚未接入")

    @override
    def _build_exercise_list_url(self, leaf_type_id: int | str, sku_id: Any = None) -> str:
        return ""
