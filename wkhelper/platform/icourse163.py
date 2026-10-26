"""中国大学MOOC (icourse163.org) 平台适配实现。"""

import hashlib
import math
import random
import re
import time
from typing import Any, override

from wkhelper.domain.accounts import UserInfo
from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError, AuthError
from wkhelper.domain.homework import Homework
from wkhelper.infra.events import emit_event
from wkhelper.infra.serialization import encode_json
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.protocol_types import HeartbeatProfile, MoocVideoContext

_CONTENT_TYPE_TO_LEAF_TYPE: dict[int, int] = {
    1: 0,
    3: 7,
    4: 3,
    5: 9,
    6: 4,
}


def _positive_video_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).isascii() or not str(value).isdigit():
        raise APIError("MOOC 返回了无效的正整数 ID", retryable=False)
    number = int(value)
    if number <= 0:
        raise APIError("MOOC 返回了非正数 ID", retryable=False)
    return number


def _unit_leaf(unit: dict[str, Any]) -> dict[str, Any]:
    content_type = int(unit.get("contentType") or 0)
    view_status = unit.get("viewStatus")
    return {
        "id": unit.get("id"),
        "name": unit.get("name") or "未命名任务",
        "leaf_type": _CONTENT_TYPE_TO_LEAF_TYPE.get(content_type, -1),
        "content_id": unit.get("contentId"),
        "content_type": content_type,
        "duration": unit.get("durationInSeconds"),
        "is_show": unit.get("visible") != 0,
        "unit_view_status": int(view_status) if view_status is not None else None,
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
    vod_heartbeat_url = "https://vod.study.163.com/submit/heartBeat"
    cookie_domain = ".icourse163.org"
    impersonate = "chrome"
    login_url = "https://www.icourse163.org/member/login.htm"
    essential_cookies = ("STUDY_SESS", "NTESSTUDYSI")
    _AUTH_SALT = "fu2s2kxcswgn5hqanx7asmlogyr5wu29"

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
                    "lesson_view_status": int(lesson["viewStatus"]) if lesson.get("viewStatus") is not None else None,
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
    async def prepare_video_context(self, video_id: str, course: Course) -> MoocVideoContext:
        """构建 MOOC 视频学习上下文。"""
        chapters = await self.get_chapter_data(course)
        target_unit: dict[str, Any] | None = None
        target_lesson_id: int | None = None
        target_lesson_view_status: int | None = None

        for ch in chapters:
            for sec in ch.get("section_list") or []:
                for leaf in sec.get("leaf_list") or []:
                    if str(leaf.get("id")) == str(video_id):
                        target_unit = leaf
                        target_lesson_id = sec.get("id")
                        target_lesson_view_status = sec.get("lesson_view_status")
                        break
                if target_unit is not None:
                    break
            if target_unit is not None:
                break

        if target_unit is None:
            raise LookupError(f"在课程 {course.id} 中未找到视频单元 {video_id}")
        if not target_unit.get("is_show", True):
            raise LookupError(f"视频单元 {video_id} 当前不可见或未发布")
        if target_unit.get("leaf_type") != 0:
            raise LookupError(f"单元 {video_id} 不是视频任务（leaf_type={target_unit.get('leaf_type')}）")

        content_id = target_unit.get("content_id")
        unit_view_status = target_unit.get("unit_view_status")
        term_id = _positive_video_id(course.metadata.get("term_id") or course.id)
        lesson_id = _positive_video_id(target_lesson_id)
        unit_id = _positive_video_id(target_unit.get("id"))
        content_id = _positive_video_id(content_id)

        result = await self._rpc_post(
            "courseBean.getLessonUnitLearnVo",
            {
                "contentId": content_id,
                "contentType": 1,
                "isDraft": 0,
                "unitId": target_unit.get("id"),
            },
        )
        if not isinstance(result, dict):
            raise APIError("获取视频单元详情失败: 平台未返回有效结果", retryable=False)

        video_vo = result.get("videoVo") if isinstance(result.get("videoVo"), dict) else {}

        raw_vid = video_vo.get("videoId") or result.get("videoId") or content_id
        video_id_int = _positive_video_id(raw_vid)
        dur_float = 0.0
        for raw_dur in (video_vo.get("duration"), result.get("duration"), target_unit.get("duration")):
            if isinstance(raw_dur, bool) or not isinstance(raw_dur, (str, int, float)):
                continue
            try:
                candidate = float(raw_dur)
            except ValueError:
                continue
            if math.isfinite(candidate) and candidate > 0:
                dur_float = candidate
                break
        if dur_float <= 0:
            raise APIError("平台未提供有限正数的视频时长", retryable=False)

        raw_start = None
        if "start" in video_vo:
            raw_start = video_vo["start"]
        elif "videoLearnTime" in result:
            raw_start = result["videoLearnTime"]

        if "start" not in video_vo and "videoLearnTime" not in result:
            start_float = 0.0
            emit_event("video.status", {"video_id": video_id, "message": "平台未提供续播位置，从头开始"})
        else:
            try:
                if isinstance(raw_start, bool) or raw_start is None:
                    raise ValueError
                start_float = float(raw_start)
                if not math.isfinite(start_float) or not 0 <= start_float <= dur_float:
                    raise ValueError
            except (TypeError, ValueError) as err:
                raise APIError("平台返回的续播位置无效或超出视频时长", retryable=False) from err

        return MoocVideoContext(
            course_id=str(course.id),
            unit_id=str(unit_id),
            term_id=term_id,
            lesson_id=lesson_id,
            video_id=video_id_int,
            duration=dur_float,
            start=start_float,
            unit_view_status=unit_view_status,
            lesson_view_status=target_lesson_view_status,
        )

    def _gene_auth_headers(self, body_bytes: bytes) -> dict[str, str]:
        """对齐前端 geneAuthHeaders：大写 MD5(body_str + nonce + timestamp + salt)。"""
        timestamp = str(int(time.time() * 1000))
        nonce = str(random.randint(1000, 9999))
        body_str = body_bytes.decode("utf-8")
        raw_sign = f"{body_str}{nonce}{timestamp}{self._AUTH_SALT}"
        signature = hashlib.md5(raw_sign.encode("utf-8")).hexdigest().upper()
        return {
            "Timestamp": timestamp,
            "Auth-Signature": signature,
            "Nonce": nonce,
            "System": "v1",
            "Content-Type": "application/json;charset=UTF-8",
        }

    async def _signed_post_once(self, endpoint: str, dto: dict[str, Any], *, timeout: float) -> None:
        """以签名 JSON 形式向课程 RPC 单次发送 DTO，不经自动重试。"""
        csrf = self._get_csrf_key()
        if not csrf:
            raise AuthError("缺少 csrfKey 凭据，无法发送课程学习记录")

        url = f"{self.api_base}/web/j/{endpoint}.rpc"
        body = encode_json({"dto": dto})
        headers = self._gene_auth_headers(body)
        headers.update(
            {
                "Origin": self.api_base,
                "Referer": f"{self.api_base}/",
            }
        )

        await self.gate.acquire()
        resp = await self._request_json_once("POST", url, params={"csrfKey": csrf}, data=body, headers=headers, timeout=timeout)
        code = resp.get("code")
        if code != 0:
            msg = str(resp.get("message") or "")
            if code == 1 and ("not_auth" in msg or "login" in msg.lower()):
                raise AuthError("中国大学MOOC 登录凭据无效或已过期")
            raise APIError(f"中国大学MOOC 学习请求失败 [{code}]: {msg or '未知错误'}", retryable=False)
        if resp.get("result") is not True:
            raise APIError(f"中国大学MOOC 学习请求未确认成功: result={resp.get('result')}", retryable=False)

    @override
    async def report_video_learning(
        self,
        context: MoocVideoContext,
        *,
        video_time: int,
        learned_seconds: int,
        finished: bool,
        index: int,
        timeout: float,
    ) -> None:
        """持续学习与完播上报原子方法。"""
        if any(type(value) is not int for value in (video_time, learned_seconds, index)):
            raise ValueError("学习位置、计数与序号必须为整数")
        if not 0 <= video_time <= context.duration:
            raise ValueError("持续学习位置必须位于视频时长范围内")
        if learned_seconds < 0:
            raise ValueError(f"学习秒数不能为负数: learned_seconds={learned_seconds}")
        if learned_seconds > 60:
            raise ValueError(f"单次学习秒数不能超过60秒: learned_seconds={learned_seconds}")
        if index <= 0:
            raise ValueError(f"上报序号必须为正整数: index={index}")

        dto = {
            "unitId": int(context.unit_id),
            "lessonId": context.lesson_id,
            "termId": context.term_id,
            "videoId": context.video_id,
            "finished": finished,
            "resolutionType": 1,
            "action": "LEARN_TIME_COUNT",
            "index": int(index),
            "videoTime": int(video_time),
            "learnedVideoTimeCount": int(learned_seconds),
        }
        await self._signed_post_once("courseRpcBean.saveMocContentLearn", dto, timeout=timeout)

    @override
    async def record_video_visit(self, context: MoocVideoContext, *, timeout: float) -> None:
        """初次访问视频单元打点原子方法。"""
        dto = {
            "unitId": int(context.unit_id),
            "finished": False,
            "resolutionType": 1,
            "contentType": 1,
            "action": "LEARN_TIME_COUNT",
            "videoTime": -1,
            "learnedVideoTimeCount": 0,
        }
        await self._signed_post_once("courseRpcBean.saveMocContentLearn", dto, timeout=timeout)

    @staticmethod
    def _detect_browser(user_agent: str) -> str:
        """对齐前端 _getBrowser 的 UA 解析顺序与后匹配覆盖规则。"""
        ua = user_agent.lower()
        browser = "undefined/undefined"
        # 无 DOM/mimeTypes 时只使用 UA，不能推测 360 插件检测结果。
        for name, pattern in (
            ("IE", r"(?:msie\s|trident.*rv:)(\d+)"),
            ("Chrome", r"chrome/(\d+)"),
            ("Firefox", r"firefox/(\d+)"),
            ("Opera", r"opera/(\d+)"),
            ("Safari", r"version/(\d+)"),
            ("Edge", r"edge/(\d+)"),
            ("QQBrowser", r"qqbrowser/(\d+)"),
        ):
            if name == "Chrome" and "safari" not in ua:
                continue
            if name == "Safari" and ("safari" not in ua or "chrome" in ua):
                continue
            match = re.search(pattern, ua)
            if match:
                browser = f"{name}/{int(match.group(1))}"
        if "micromessenger" in ua:
            browser = "WeixinBrowser/NaN"
        return browser

    @override
    async def send_video_heartbeat(self, context: MoocVideoContext, *, timeout: float) -> None:
        """发送 VOD 播放器遥测心跳原子方法。"""
        if self.user is None:
            raise AuthError("未登录 MOOC，无法发送 VOD 心跳")
        user_id = self.user.id

        ua = self.client.headers.get("User-Agent") or ""
        device_name = self._detect_browser(ua)

        payload = {
            "errorCode": None,
            "errorMsg": None,
            "preLoadTime": 0,
            "currentKbps": 0,
            "userId": user_id,
            "videoId": context.video_id,
            "rate": 1,
            "deviceName": device_name,
            "source": "web",
            "time": int(time.time() * 1000),
        }
        body = encode_json(payload)
        headers = {
            "Content-Type": "application/json",
            "Origin": self.api_base,
            "Referer": f"{self.api_base}/",
        }
        response = await self.client.post(self.vod_heartbeat_url, data=body, headers=headers, timeout=timeout)
        response.raise_for_status()

    @override
    async def prepare_submit_context(self, homework: Homework, course: Course) -> dict[str, Any]:
        raise NotImplementedError("中国大学MOOC作业提交上下文尚未接入")

    @override
    def _build_exercise_list_url(self, leaf_type_id: int | str, sku_id: Any = None) -> str:
        return ""
