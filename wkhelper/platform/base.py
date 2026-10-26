"""平台基础接口与课件树抽象。"""

import asyncio
import json
import logging
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

from pydantic import JsonValue

from wkhelper.domain.accounts import UserInfo
from wkhelper.domain.actions import NextAction
from wkhelper.domain.content import (
    NoticeDetail,
)
from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError, RiskControlError
from wkhelper.domain.exam import (
    ExamCoverDetail,
    ExamPaperData,
    ExamProblemItem,
    ExamResultData,
    ExamStartResult,
    ExamStatusData,
)
from wkhelper.domain.homework import Homework, OptionItem, QuestionSubmitOutcome
from wkhelper.domain.video import VideoContext
from wkhelper.infra.clock import ServerClock
from wkhelper.infra.http import (
    AsyncSession,
    HttpMethod,
    request_json,
    request_json_once,
)
from wkhelper.infra.markdown import html_to_md, html_to_md_with_images
from wkhelper.infra.throttle import RateGate, RetryPolicy
from wkhelper.platform.parsing import parse_exercise_response
from wkhelper.platform.protocol_types import (
    HeartbeatProfile,
    MoocVideoContext,
    PlatformExamCacheData,
    PlatformExamCoverData,
    PlatformExamPaperData,
    PlatformExamProblemRaw,
    PlatformExamResult,
    create_page_nonce,
)

logger = logging.getLogger(__name__)


def _as_int_or_str(value: Any) -> int | str:
    """数字形态的 ID 保持数字，前端同样按 `parseInt` 语义传参。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


# 平台风控拦截码：命中后必须人工完成人机验证，自动重试只会加重处罚


class BasePlatform(ABC):
    """所有平台的抽象基类。"""

    # 平台 API 端点基址与路径前缀，供通用 URL 构造复用；子类以类属性声明。
    api_base: ClassVar[str] = ""
    api_prefix: ClassVar[str] = ""
    # 默认 TLS 指纹伪装目标；`None` 表示不伪装（会话 Cookie 与签发指纹绑定时必须一致）
    impersonate: ClassVar[str | None] = "chrome"
    # 会话 Cookie 的写入域；空串表示由 CookieJar 绑定当前 host
    cookie_domain: ClassVar[str] = ""
    # 浏览器登录页面 URL 与核心登录态 Cookie 契约
    login_url: ClassVar[str] = ""
    essential_cookies: ClassVar[tuple[str, ...]] = ()

    def __init__(
        self,
        client: AsyncSession,
        *,
        gate: RateGate | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.client = client
        self.user: UserInfo | None = None
        self.platform_code: str | None = None
        self.account_id: str | None = None
        self._exam_room_bindings: dict[str, tuple[str, int, str]] = {}
        self.current_cookies: dict[str, str] | None = None
        self.gate = gate or RateGate()
        self.retry_policy = retry_policy or RetryPolicy()
        self.clock = ServerClock()

    @property
    def clock_offset_ms(self) -> float:
        """已测量的服务器时钟偏移（毫秒），正数表示本机时间慢于平台。"""
        return self.clock.offset_ms

    async def _request_json(self, method: HttpMethod, url: str, **kwargs: Any) -> dict[str, Any]:
        return await request_json(self.client, self.gate, self.retry_policy, self.clock, method, url, **kwargs)

    async def _request_json_once(self, method: HttpMethod, url: str, **kwargs: Any) -> dict[str, Any]:
        return await request_json_once(self.client, self.clock, method, url, **kwargs)

    # 平台抽象方法
    @abstractmethod
    async def login_with_cookies(self, cookies: dict[str, str]) -> UserInfo: ...

    @abstractmethod
    async def get_courses(self) -> list[Course]: ...

    @abstractmethod
    async def get_chapter_data(self, course: Course) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_leaf_schedules(self, course: Course) -> dict[int, float]: ...

    async def get_course_progress(self, course: Course) -> list[dict[str, Any]]:
        """获取课程大纲与节点进度树原始结构（雨课堂 c27 进度树等）。未实现时抛出异常。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现课程进度树接口")

    async def get_leaf_info(self, leaf_id: str | int, course: Course) -> dict[str, Any]:
        """获取课件叶子节点详细元数据。"""
        url = self._build_leaf_info_url(leaf_id, course)
        params = self._get_leaf_info_params(leaf_id, course)
        kwargs = self.get_request_kwargs(course) or {}
        resp = await self._request_json("GET", url, params=params, **kwargs)
        data = resp.get("data")
        return data if isinstance(data, dict) else {}

    def _classroom_id(self, course: Course) -> str:
        """课程所属课堂 ID，两个平台都以 `metadata['classroom_id']` 承载。"""
        return str(course.metadata["classroom_id"])

    def _build_leaf_info_url(self, leaf_id: str | int, course: Course) -> str:
        """课件叶子详情接口，两个平台仅路径前缀不同。"""
        return f"{self.api_base}/{self.api_prefix}/v1/lms/learn/leaf_info/{self._classroom_id(course)}/{leaf_id}/"

    def _get_leaf_info_params(self, leaf_id: str | int, course: Course) -> dict[str, Any] | None:
        return None

    def _build_exercise_list_url(self, leaf_type_id: int | str, sku_id: Any = None) -> str:
        """习题列表接口；前端在学堂在线按 `{exercise_id}/{sku_id}/` 双段寻址。"""
        return f"{self.api_base}/{self.api_prefix}/v1/lms/exercise/get_exercise_list/{leaf_type_id}/"

    def _build_submit_url(self) -> str:
        return f"{self.api_base}/{self.api_prefix}/v1/lms/exercise/problem_apply/"

    def _build_submit_payload(
        self,
        problem_id: int,
        answer: list[str],
        submit_ctx: dict[str, Any] | None = None,
        question: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构建提交载荷；题型差异（如填空需 `answers` 映射）由平台实现处理。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现题目提交载荷")

    def format_web_task_url(self, course: Course, leaf_id: str | int, task_type: str = "exercise") -> str | None:
        """没有网页入口的平台返回空值。"""
        return None

    def get_request_kwargs(self, course: Course) -> dict[str, Any] | None:
        return None

    async def get_completed_video_ids(self, course: Course) -> set[int] | None:
        """返回平台权威的「已完成视频 ID」集合。

        返回 `None` 表示平台没有更可靠的来源，基类退回用 `get_leaf_schedules`
        的章节进度值判断。平台实现不要用空集合表示"未知"。
        """
        return None

    @staticmethod
    def _normalize_answer(raw: Any) -> list[str]:
        """把平台返回的答案值统一为字符串列表。"""
        if isinstance(raw, list):
            return [str(item) for item in raw]
        if raw is None:
            return []
        return [str(raw)]

    def _parse_submit_response(self, data: dict[str, Any]) -> QuestionSubmitOutcome:
        """解析作业提交响应。"""
        if data.get("success") is True:
            result = data.get("data", {})
            return QuestionSubmitOutcome(
                success=True,
                is_correct=bool(result.get("is_right", result.get("is_correct", False))),
                correct_answer=self._normalize_answer(result.get("answer", [])),
            )
        msg = data.get("msg") or data.get("message") or "平台返回提交失败"
        code = data.get("error_code")
        error_msg = f"{msg} (error_code: {code})" if code is not None else str(msg)
        return QuestionSubmitOutcome(
            success=False,
            is_correct=False,
            correct_answer=[],
            error=error_msg,
            error_code=code if isinstance(code, int) else None,
        )

    # 模板方法

    @staticmethod
    def _is_exam_overdue(deadline: Any) -> bool:
        """截止时间已过（毫秒时间戳）即视为逾期。"""
        try:
            return deadline is not None and int(deadline) < int(time.time() * 1000)
        except (TypeError, ValueError):
            return False

    async def get_exam_cover(self, leaf_id: str | int, course: Course) -> ExamCoverDetail:
        """获取考试封面与考场元数据。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现考试封面接口")

    @staticmethod
    def _ensure_exam_leaf(leaf_id: str | int, leaf_type: Any) -> None:
        """考试命令只允许作用于 leaf_type=5；响应缺字段时放行以兼容精简接口。"""
        if leaf_type is not None and leaf_type != 5:
            raise APIError(
                f"节点 {leaf_id} 的 leaf_type={leaf_type} 不是考试（5=考试）；章节作业请改用 `homework` / `question` 命令",
                retryable=False,
            )

    async def start_exam(self, leaf_id: str | int, course: Course) -> ExamStartResult:
        """进入考场并申请考试授权令牌。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现进入考场接口")

    def bind_exam_room(self, host: str, eid: int, login_url: str, *, leaf_id: str | int) -> None:
        """为指定考试节点绑定已有考场，避免重新换票挤下浏览器。"""
        self._exam_room_bindings[str(leaf_id)] = (host, eid, login_url)

    async def _open_exam_room(self, leaf_id: str | int, course: Course) -> tuple[str, int, str]:
        """返回考场地址、ID 与直达 URL；只复用当前节点的显式绑定。"""
        binding = self._exam_room_bindings.get(str(leaf_id))
        if binding is not None:
            return binding
        return await self._mint_exam_room(leaf_id, course)

    async def _mint_exam_room(self, leaf_id: str | int, course: Course) -> tuple[str, int, str]:
        """申请并建立新的考场会话；子类实现。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现考场令牌接口")

    @staticmethod
    def _exam_status_text(raw: Any) -> str:
        """考场 result.status 归一：1 答题中，2-5 已交卷，6 已缺考，0 未开始，其余原样。"""
        if raw in (2, 3, 4, 5):
            return "已交卷"
        if raw == 1:
            return "答题中"
        if raw == 6:
            return "已缺考"
        if raw == 0:
            return "未开始"
        return str(raw) if raw is not None else "未知"

    async def get_exam_status_snapshot(self, leaf_id: str | int, course: Course) -> ExamStatusData:
        """只读汇总考场各接口状态，用于定位「页面已提交但成绩未落库」。"""
        host, eid, _login_url = await self._open_exam_room(leaf_id, course)

        cover = await self._request_json("GET", f"{host}/exam_room/cover", params={"exam_id": eid})
        cover_raw = cover.get("data") if isinstance(cover.get("data"), dict) else {}
        cover_data = PlatformExamCoverData.model_validate(cover_raw)
        result_status = cover_data.result.status if cover_data.result else None
        room_status = self._exam_status_text(result_status)
        room_status_raw = result_status if isinstance(result_status, int) else None

        try:
            lms_cover = await self.get_exam_cover(leaf_id, course)
            lms_status = lms_cover.raw_status
        except Exception:
            lms_status = None

        refresh = await self._request_json("GET", f"{host}/exam_room/refresh_time", params={"exam_id": eid})
        refresh_code = refresh.get("errcode") if isinstance(refresh.get("errcode"), int) else None
        refresh_message = str(refresh.get("errmsg") or "")

        cached = await self._request_json("GET", f"{host}/exam_room/cache_results", params={"exam_id": eid})
        cached_raw = cached.get("data") if isinstance(cached.get("data"), dict) else {}
        cached_data = PlatformExamCacheData.model_validate(cached_raw)
        cached_count = len(cached_data.results)
        cached_sample = json.dumps(cached_data.results[0].model_dump(), ensure_ascii=False) if cached_data.results else ""

        problem = await self._request_json("GET", f"{host}/exam_room/problem_results", params={"exam_id": eid})
        result_code = problem.get("errcode") if isinstance(problem.get("errcode"), int) else None
        result_message = str(problem.get("errmsg") or "")

        return ExamStatusData(
            exam_id=str(leaf_id),
            room_status=room_status,
            room_status_raw=room_status_raw,
            lms_status=lms_status,
            refresh_code=refresh_code,
            refresh_message=refresh_message,
            cached_count=cached_count,
            cached_sample=cached_sample,
            result_code=result_code,
            result_message=result_message,
        )

    async def get_exam_cached_answers(self, leaf_id: str | int, course: Course) -> dict[str, list[str]]:
        """读取考场暂存作答（只读），返回 {题目 ID: 答案列表}。"""
        host, eid, _login_url = await self._open_exam_room(leaf_id, course)
        resp = await self._request_json("GET", f"{host}/exam_room/cache_results", params={"exam_id": eid})
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        cached_data = PlatformExamCacheData.model_validate(data)
        staged: dict[str, list[str]] = {}
        for item in cached_data.results:
            if item.problem_id is None:
                continue
            raw = item.result
            if isinstance(raw, list):
                answers = [str(value) for value in raw if str(value).strip()]
            elif raw is None or str(raw).strip() == "":
                answers = []
            else:
                answers = [str(raw)]
            if answers:
                staged[str(item.problem_id)] = answers
        return staged

    async def get_exam_result(self, leaf_id: str | int, course: Course, cover: ExamCoverDetail | None = None) -> ExamResultData:
        """读取考场成绩与作答结果（只读）；未交卷时平台返回空数据。"""
        host, eid, _login_url = await self._open_exam_room(leaf_id, course)
        resp = await self._request_json("GET", f"{host}/exam_room/problem_results", params={"exam_id": eid})
        resp_data = resp.get("data")
        raw_data = resp_data if isinstance(resp_data, dict) else {}
        result = PlatformExamResult.model_validate(raw_data)

        errcode = int(resp.get("errcode") or 0)
        errmsg = str(resp.get("errmsg") or "")
        message = f"{errmsg} (errcode: {errcode})".strip()
        if errcode == 10107:
            is_submitted = cover is not None and ((cover.raw_status is not None and cover.raw_status > 1) or cover.status == "已交卷")
            if is_submitted:
                message = "试卷已交卷但进度异常（平台返回 10107），建议联系任课老师核对作答与提交记录"
            else:
                message = "答卷尚未提交，暂无成绩"

        return ExamResultData(
            exam_id=str(leaf_id),
            grade=result.grade,
            duration=result.duration,
            problem_count=len(result.problem_results),
            message=message,
        )

    async def get_exam_paper(self, leaf_id: str | int, course: Course) -> ExamPaperData:
        """读取考场子系统下发的整卷题目；风控拦截时拉起浏览器由用户完成滑块后截获。"""
        host, eid, _login_url = await self._open_exam_room(leaf_id, course)
        try:
            payload = await self._request_json("GET", f"{host}/exam_room/show_paper", params={"exam_id": eid})
        except RiskControlError:
            from wkhelper.infra.browser.challenge import find_chromium_browser, solve_captcha_interactive

            if not find_chromium_browser():
                raise
            payload, fresh = await solve_captcha_interactive(
                web_url=f"{host}/exam/{eid}?isFrom=0&language=zh",
                target_api_pattern="exam_room/show_paper",
                cookies={k: v for k, v in self.client.cookies.items()},
                cookie_domain="examination.xuetangx.com",
            )
            for key, value in fresh.items():
                self.client.cookies.set(key, value)

        paper_raw = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        paper = PlatformExamPaperData.model_validate(paper_raw)
        problems = [self._parse_exam_problem(item, index) for index, item in enumerate(paper.problems, 1)]
        cover = await self.get_exam_cover(leaf_id, course)
        return ExamPaperData(
            course_id=str(course.id),
            exam_id=str(leaf_id),
            title=paper.title or cover.title,
            problems=problems,
        )

    @staticmethod
    def _parse_exam_problem(item: Any, index: int) -> ExamProblemItem:
        """把考场试卷的题目条目归一为对外模型。"""
        raw = item if isinstance(item, dict) else {}
        prob = PlatformExamProblemRaw.model_validate(raw)
        options = [OptionItem(key=opt.key, text=html_to_md(opt.value)) for opt in prob.Options]
        raw_answer = prob.Answer
        answers = [str(a) for a in raw_answer] if isinstance(raw_answer, list) else []
        type_text = str(prob.TypeText or prob.Type or "")
        prompt, images = html_to_md_with_images(prob.Body or "")
        score_val = prob.score if prob.score is not None else prob.Score
        return ExamProblemItem(
            index=index,
            id=str(prob.problem_id or index),
            type=type_text or None,
            prompt=prompt,
            images=images,
            options=options,
            score=float(score_val or 0.0),
            answer=answers,
            has_remark=bool(prob.HasRemark),
        )

    async def mark_article_finish(self, leaf_id: str | int, course: Course) -> bool:
        """标记资料节点已完成；平台差异仅在查询参数，由 `_article_finish_params` 提供。"""
        url = f"{self.api_base}/{self.api_prefix}/v1/lms/learn/user_article_finish/{leaf_id}/"
        kwargs = self.get_request_kwargs(course) or {}
        resp = await self._request_json("GET", url, params=self._article_finish_params(course), **kwargs)
        return bool(resp.get("success", False))

    def _article_finish_params(self, course: Course) -> dict[str, Any] | None:
        return None

    async def discussion_leaf_completed(self, leaf_id: str | int, course: Course) -> bool | None:
        """按平台发言状态判定讨论节点是否完成；无法廉价判定时返回 None 回退章节进度。"""
        return None

    async def get_notice_detail(self, leaf_id: str | int, course: Course) -> NoticeDetail:
        """获取公告正文与发布信息；两个平台的公告接口族不同，由平台实现。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现公告详情接口")

    async def fetch_discussion_unit(self, leaf_id: str | int, course: Course) -> dict[str, Any]:
        """拉取讨论题单元信息；两个平台的讨论接口族完全不同，由平台实现。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现讨论题单元接口")

    async def discussion_completed(self, topic_info: dict[str, Any], course: Course, leaf_id: str | int) -> bool:
        """讨论题是否已打卡；默认按本人评论数判断。"""
        return int(topic_info.get("user_comment_num") or 0) > 0

    async def post_discussion_comment(
        self,
        topic_info: dict[str, Any],
        content: str,
        course: Course,
        leaf_id: str | int,
    ) -> dict[str, Any]:
        """发送讨论发言，返回平台原始响应。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现讨论发言接口")

    async def delete_discussion_comment(
        self,
        comment_id: str | int,
        course: Course,
    ) -> dict[str, Any]:
        """删除讨论题发言，返回平台原始响应。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现删除讨论发言接口")

    async def get_leaf_questions(self, leaf_id: str | int, course: Course) -> list[dict[str, Any]]:
        """获取指定叶子节点的习题列表。"""
        data = await self.get_leaf_info(leaf_id, course)
        leaf_type = data.get("leaf_type") if isinstance(data, dict) else None
        # leaf_type 以平台前端为准：5=考试，6=作业（走习题接口），7=课件，8=线下课堂，9=随堂测验，10=公告
        if leaf_type == 5:
            raise APIError(
                f"节点 {leaf_id} 是考试（leaf_type=5），普通习题接口不提供考试题目；"
                "请使用 `exam inspect` 查看考试信息，并在浏览器中完成考试",
                retryable=False,
            )
        if leaf_type in (7, 8, 9, 10):
            raise APIError(
                f"节点 {leaf_id} 是 leaf_type={leaf_type}（7=课件、8=线下课堂、9=随堂测验、10=公告），不属于可导出的章节作业",
                retryable=False,
            )
        content_info = data.get("content_info") if isinstance(data, dict) else None
        leaf_type_id = (content_info.get("leaf_type_id") if isinstance(content_info, dict) else None) or (
            data.get("leaf_type_id") if isinstance(data, dict) else None
        )
        if not leaf_type_id:
            return []
        sku_id = data.get("sku_id") if isinstance(data, dict) else None

        kwargs = self.get_request_kwargs(course) or {}
        ex_url = self._build_exercise_list_url(leaf_type_id, sku_id)
        try:
            q_resp = await self._request_json("GET", ex_url, **kwargs)
        except RiskControlError as exc:
            web_url = self.format_web_task_url(course, leaf_id, "exercise")
            from wkhelper.infra.browser.challenge import find_chromium_browser, solve_captcha_interactive

            if web_url and find_chromium_browser():
                cookie_domain = ".xuetangx.com" if "xuetangx" in self.api_base else ".yuketang.cn"
                current_cookies = {k: v for k, v in self.client.cookies.items()}
                try:
                    q_resp, new_cookies = await solve_captcha_interactive(
                        web_url=web_url,
                        target_api_pattern=f"get_exercise_list/{leaf_type_id}",
                        cookies=current_cookies,
                        cookie_domain=cookie_domain,
                    )
                    if new_cookies:
                        for k, v in new_cookies.items():
                            self.client.cookies.set(k, v)
                except RiskControlError:
                    raise
                except Exception as inner_e:
                    logger.warning(f"交互式滑块穿透异常: {inner_e}")
                    next_actions = [
                        NextAction(reason="在浏览器打开该任务页面完成滑块人机验证后重试", argv=list(sys.argv), description=web_url)
                    ]
                    raise RiskControlError(
                        f"获取题目列表触发平台风控人机验证: {ex_url}", web_url=web_url, next_actions=next_actions
                    ) from exc
            else:
                next_actions = (
                    [NextAction(reason="在浏览器打开该任务页面完成滑块人机验证后重试", argv=list(sys.argv), description=web_url or "")]
                    if web_url
                    else None
                )
                raise RiskControlError(f"获取题目列表触发平台风控人机验证: {ex_url}", web_url=web_url, next_actions=next_actions) from exc
        questions = parse_exercise_response(q_resp)
        q_data = q_resp.get("data")
        if isinstance(q_data, dict):
            font_url = q_data.get("font")
            if font_url and questions:
                try:
                    from wkhelper.platform.deobfuscator import deobfuscate_questions

                    await deobfuscate_questions(questions, font_url, self.fetch_bytes)
                except Exception as e:
                    logger.warning(f"题目反混淆失败，返回原文: {e}")
        return questions

    async def submit_answer(
        self,
        homework_name: str,
        problem_id: int,
        answer: list[str] | str,
        kwargs: dict[str, Any] | None = None,
        submit_ctx: dict[str, Any] | None = None,
        on_status: Callable[[str], None] | None = None,
        question: dict[str, Any] | None = None,
    ) -> QuestionSubmitOutcome:
        """提交单个题目答案：统一走节流器，并按平台限流提示等待后重试。"""
        url = self._build_submit_url()
        if isinstance(answer, str):
            answer = [answer]
        payload = self._build_submit_payload(problem_id, answer, submit_ctx=submit_ctx, question=question)

        max_attempts = max(1, self.retry_policy.max_attempts) + 6
        for attempt in range(1, max_attempts + 1):
            await self.gate.acquire()
            try:
                parsed = await self._request_json_once("POST", url, json=payload, **(kwargs or {}))
            except APIError as exc:
                if not exc.retryable or attempt >= max_attempts:
                    raise
                if exc.retry_after is not None or exc.status_code == 429:
                    wait = exc.retry_after if exc.retry_after is not None else self.gate.backoff_delay(attempt, self.retry_policy)
                    if on_status:
                        on_status(f"限流等待 {wait:.0f}s")
                    await self.gate.cooldown(wait)
                elif exc.status_code is not None and exc.status_code >= 500:
                    await asyncio.sleep(self.gate.backoff_delay(attempt, self.retry_policy))
                else:
                    raise
                continue
            return self._parse_submit_response(parsed)

        raise APIError(f"题目 {problem_id} 提交重试次数过多，平台持续限流", retryable=True)

    async def fetch_bytes(self, url: str) -> bytes:
        """下载远程资源字节。

        导出与反混淆模块仅消费 infra.http.Fetcher，避免耦合具体 HTTP 客户端。
        """
        response = await self.client.get(url, timeout=15, headers={"Accept": "*/*"})
        response.raise_for_status()
        return bytes(response.content or b"")

    @abstractmethod
    async def prepare_video_context(self, video_id: str, course: Course) -> VideoContext | MoocVideoContext: ...

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
        """上报视频学习记录（MOOC 平台实现）。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现视频学习上报接口")

    async def record_video_visit(self, context: MoocVideoContext, *, timeout: float) -> None:
        """初次访问视频单元打点（MOOC 平台实现）。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现视频初访打点接口")

    async def send_video_heartbeat(self, context: MoocVideoContext, *, timeout: float) -> None:
        """发送 VOD 播放器心跳（MOOC 平台实现）。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现 VOD 心跳接口")

    @property
    @abstractmethod
    def heartbeat_profile(self) -> HeartbeatProfile:
        """本平台心跳报文的固定字段，缺失实现时启动即报错。"""

    def build_heartbeat_payload(
        self,
        video_id: str,
        classroom_id: str,
        user_id: int,
        course_id: int,
        sku_id: int,
        video_frame: float,
        seq: int,
        timestamp: int,
        cc: str,
        duration: float,
        page_nonce: str | None = None,
    ) -> dict[str, JsonValue]:
        """构建单个心跳打点，字段语义对齐 Web 播放器（见前端 media-log 定义）。

        `pg` 在同一次观看会话内固定（前端播放器实例生命周期内只生成一次）。
        """
        profile = self.heartbeat_profile
        nonce = page_nonce or create_page_nonce()
        return {
            "i": profile.interval,
            "et": "heartbeat",
            "p": "web",
            "n": profile.n,
            "lob": profile.lob,
            "cp": round(float(video_frame), 1),
            "fp": 0,
            "tp": 0,
            "sp": profile.sp,
            "ts": str(timestamp + round(self.clock.offset_ms)),
            "u": user_id,
            "uip": "",
            "c": course_id,
            "v": _as_int_or_str(video_id),
            "skuid": _as_int_or_str(sku_id),
            "classroomid": classroom_id,
            "cc": cc,
            "d": round(float(duration), 1),
            "pg": f"{video_id}_{nonce}",
            "sq": seq,
            "t": "video",
            "cards_id": 0,
            "slide": 0,
            "v_url": "",
        }

    @abstractmethod
    async def prepare_submit_context(self, homework: Homework, course: Course) -> dict[str, Any]: ...
