"""平台基础接口与课件树抽象。"""

import asyncio
import json
import logging
import random
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from wkhelper.core.config import MAX_WORKERS_STATUS
from wkhelper.core.entities import Course, Homework, UserInfo, VideoContext
from wkhelper.core.events import EventCallback, emit_event, is_debug_enabled
from wkhelper.core.exceptions import APIError, AuthError, RiskControlError
from wkhelper.core.homework import generic_submit_homework
from wkhelper.core.json_types import JsonDict
from wkhelper.core.markdown import html_to_md, html_to_md_with_images
from wkhelper.core.qr_login import QrCodeSession
from wkhelper.core.results import (
    ArticleItem,
    ArticleReadResult,
    DiscussionCommentResult,
    DiscussionItem,
    DiscussionTopicDetail,
    ExamAnswerResult,
    ExamCoverDetail,
    ExamItem,
    ExamPaperData,
    ExamProblemItem,
    ExamStartResult,
    ExamSubmitResult,
    HomeworkSubmitResult,
    NextAction,
    OptionItem,
    QuestionSubmitOutcome,
    QuestionSubmitResult,
    VideoProgress,
    VideoWatchResult,
)
from wkhelper.core.throttle import RateGate, RetryPolicy, parse_retry_hint
from wkhelper.core.time_types import epoch_millis, parse_http_date_ms
from wkhelper.core.transport import (
    AsyncSession,
    HttpMethod,
    build_session,
)
from wkhelper.core.transport import (
    ConnectionError as HttpConnectionError,
)
from wkhelper.core.transport import (
    HTTPError as HttpError,
)
from wkhelper.core.transport import (
    Timeout as HttpTimeout,
)
from wkhelper.core.video import VideoWatchOptions, generic_watch_video, parse_progress_text

logger = logging.getLogger(__name__)

type LeafContext = tuple[dict[str, Any], str, str | None]


def _page_nonce() -> str:
    """页面编号后缀，算法对齐前端 `Math.floor(1048576*(1+random)).toString(36)`。"""
    return _base36(int(1048576 * (1 + random.random())))


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value <= 0:
        return "0"
    out: list[str] = []
    while value:
        value, rem = divmod(value, 36)
        out.append(digits[rem])
    return "".join(reversed(out))


def _as_int_or_str(value: Any) -> int | str:
    """数字形态的 ID 保持数字，前端同样按 `parseInt` 语义传参。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


# 平台风控拦截码：命中后必须人工完成人机验证，自动重试只会加重处罚
_RISK_CONTROL_CODE = "400403"


def _looks_like_risk_control(body: str | dict[str, Any] | None) -> bool:
    """识别风控人机验证响应（`error_code: 400403`）。"""
    if isinstance(body, dict):
        return str(body.get("error_code") or "") == _RISK_CONTROL_CODE
    if isinstance(body, str):
        return _RISK_CONTROL_CODE in body
    return False


def _header_value(headers: Any, name: str) -> str | None:
    """按不区分大小写的方式读取响应头（兼容普通 dict 与 Headers 对象）。"""
    if not headers:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    return getter(name) or getter(name.title()) or getter(name.upper())


@dataclass(frozen=True)
class HeartbeatProfile:
    """心跳报文中随平台变化的固定字段，由平台适配层显式提供。

    `lob` 是业务线标识（雨课堂 `ykt`、学堂在线 `xt`）；`n` 是 CDN 代号，
    只有解析出播放源时才有意义，未知时留空；`sp` 为播放倍速。
    """

    lob: str
    n: str = ""
    sp: float = 1.0
    interval: int = 5


class BasePlatformParser:
    """平台通用树结构与响应解析器。"""

    @staticmethod
    def format_leaf_label(
        leaf: dict[str, Any],
        chapter_name: str,
        section_name: str | None,
    ) -> str:
        """构建树形展示标签，避免同名任务导致选择歧义。"""
        raw_name = str(leaf.get("name") or "未命名任务").strip()
        if raw_name.lower() == "video" and section_name:
            raw_name = section_name

        parts = [chapter_name]
        if section_name and section_name != chapter_name:
            parts.append(section_name)
        parts.append(raw_name)
        return f"{' / '.join(parts)} (ID:{leaf.get('id')})"

    @staticmethod
    def iter_leaves_with_context(chapter_data: list[dict[str, Any]]) -> Iterator[LeafContext]:
        """递归遍历课件树，支持单层、多层嵌套及混合结构，保留完整章节上下文。"""

        def _traverse(node: dict[str, Any], chapter: str, section: str | None) -> Iterator[LeafContext]:
            name = str(node.get("name") or "").strip()
            children = node.get("section_leaf_list") or node.get("section_list") or node.get("leaf_list") or node.get("units")

            if isinstance(children, list) and children:
                curr_chapter = chapter or name or "未命名章节"
                curr_section = name if chapter else section
                for child in children:
                    if isinstance(child, dict):
                        yield from _traverse(child, curr_chapter, curr_section)
            else:
                yield node, chapter or "未命名章节", section

        for root_item in chapter_data:
            if not isinstance(root_item, dict):
                continue
            ch_name = str(root_item.get("name") or "未命名章节")
            children = (
                root_item.get("section_leaf_list") or root_item.get("section_list") or root_item.get("leaf_list") or root_item.get("units")
            )
            if isinstance(children, list) and children:
                for sub in children:
                    if isinstance(sub, dict):
                        yield from _traverse(sub, ch_name, sub.get("name"))
            elif "leaf_type" in root_item:
                yield root_item, ch_name, None

    @staticmethod
    def parse_cookie_string(raw: str) -> dict[str, str]:
        """解析浏览器 cookie 字符串为字典。"""
        result: dict[str, str] = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()
        return result

    @staticmethod
    def parse_leaf_schedules(raw: Any) -> dict[int, float]:
        """把 `leaf_schedules` 原始映射解析为 int 叶子 ID -> 进度。"""
        if not isinstance(raw, dict):
            return {}
        schedules: dict[int, float] = {}
        for leaf_id, progress in raw.items():
            try:
                schedules[int(leaf_id)] = float(progress)
            except (TypeError, ValueError):
                continue
        return schedules

    @staticmethod
    def parse_exercise_response(q_resp: dict[str, Any]) -> list[dict[str, Any]]:
        """解析习题 API 响应中的题目列表，兼容多种响应结构。"""
        if q_resp.get("success") is False:
            error_msg = q_resp.get("msg", "Unknown error")
            logger.warning(f"API 返回错误: {error_msg}")
            return []

        data = q_resp.get("data", q_resp)
        if isinstance(data, dict):
            if "problems" in data:
                return data["problems"]
        if isinstance(data, list):
            return data
        return []


class BasePlatform(ABC):
    """所有平台的抽象基类。"""

    # 平台 API 端点基址与路径前缀，供通用 URL 构造复用；子类以类属性声明。
    api_base: ClassVar[str] = ""
    api_prefix: ClassVar[str] = ""
    # 默认 TLS 指纹伪装目标；`None` 表示不伪装（会话 Cookie 与签发指纹绑定时必须一致）
    impersonate: ClassVar[str | None] = "chrome"

    def __init__(
        self,
        client: AsyncSession,
        *,
        gate: RateGate | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.client = client
        self.user: UserInfo | None = None
        self.current_cookies: dict[str, str] | None = None
        self.gate = gate or RateGate()
        self.retry_policy = retry_policy or RetryPolicy()
        self._clock_offset_ms = 0.0
        self._clock_samples = 0

    @property
    def clock_offset_ms(self) -> float:
        """已测量的服务器时钟偏移（毫秒），正数表示本机时间慢于平台。"""
        return self._clock_offset_ms

    def _server_now_ms(self) -> int:
        """按服务器时钟换算的当前毫秒时间戳。"""
        return int(time.time() * 1000 + self._clock_offset_ms)

    @staticmethod
    def _trace_http(method: str, url: str, start: float, status_code: int | None, error: str | None = None) -> None:
        """仅在调试模式下发射 HTTP 追踪事件，避免污染标准输出。"""
        if not is_debug_enabled():
            return
        payload: JsonDict = {
            "method": method.upper(),
            "url": url,
            "status_code": status_code,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
        }
        if error is not None:
            payload["error"] = error
        emit_event("http.trace", payload)

    async def _request_json(self, method: HttpMethod, url: str, **kwargs: Any) -> dict[str, Any]:
        """发起 HTTP 请求并解析 JSON；瞬时错误按退避与冷却策略重试。"""
        policy = self.retry_policy
        for attempt in range(1, policy.max_attempts + 1):
            await self.gate.acquire()
            try:
                return await self._request_json_once(method, url, **kwargs)
            except APIError as exc:
                if not exc.retryable or attempt >= policy.max_attempts:
                    raise
                delay = exc.retry_after if exc.retry_after is not None else self.gate.backoff_delay(attempt, policy)
                logger.debug(f"{method} {url} 第 {attempt} 次失败，{delay:.2f}s 后重试: {exc}")
                if exc.retry_after is not None:
                    await self.gate.cooldown(delay)
                else:
                    await asyncio.sleep(delay)
        raise APIError(f"请求失败次数过多: {url}")  # 循环必然在内部返回或抛出

    async def _request_json_once(self, method: HttpMethod, url: str, **kwargs: Any) -> dict[str, Any]:
        """单次请求：HTTP 失败与限流提示统一收敛为带重试元数据的错误。"""
        start = time.perf_counter()
        try:
            resp = await self.client.request(method, url, **kwargs)
        except (HttpConnectionError, HttpTimeout) as exc:
            self._trace_http(method, url, start, None, str(exc))
            raise APIError(f"网络请求失败: {url}") from exc

        body = resp.text or ""
        try:
            resp.raise_for_status()
        except HttpError as exc:
            self._trace_http(method, url, start, resp.status_code, str(exc))
            if _looks_like_risk_control(body):
                raise RiskControlError(f"触发平台风控人机验证，请先在浏览器完成验证后重试: {url}") from exc
            if resp.status_code in (401, 403):
                raise AuthError(f"平台拒绝访问（HTTP {resp.status_code}），登录态可能已失效: {url}") from exc
            raise APIError(
                f"平台请求失败（HTTP {resp.status_code}）: {url}",
                status_code=resp.status_code,
                retry_after=parse_retry_hint(body, resp.headers.get("Retry-After")),
                retryable=resp.status_code == 429 or resp.status_code >= 500,
            ) from exc

        hint = parse_retry_hint(body, resp.headers.get("Retry-After"))
        if hint is not None:
            self._trace_http(method, url, start, resp.status_code, "rate limited")
            raise APIError(f"平台限流，需等待 {hint:.0f}s: {url}", status_code=resp.status_code, retry_after=hint)

        try:
            payload = resp.json()
        except ValueError as exc:
            self._trace_http(method, url, start, resp.status_code, str(exc))
            raise APIError(f"平台响应不是合法 JSON: {url}") from exc
        self._trace_http(method, url, start, resp.status_code)
        if not isinstance(payload, dict):
            raise APIError(f"平台响应不是 JSON 对象: {url}")
        if _looks_like_risk_control(payload):
            raise RiskControlError(f"触发平台风控人机验证，请先在浏览器完成验证后重试: {url}")
        self._sync_server_clock(resp.headers, payload)
        return payload

    _MAX_CLOCK_SKEW_MS = 6 * 3600 * 1000
    _CLOCK_SMOOTHING = 0.3

    def _sync_server_clock(self, headers: Any, payload: dict[str, Any]) -> None:
        """用响应中的 server_time 或 Date 头校正本机时钟，供心跳与作答时间戳使用。"""
        server_ms = self._payload_server_time_ms(payload)
        if server_ms is None:
            server_ms = parse_http_date_ms(_header_value(headers, "date"))
        if server_ms is None:
            return
        sample = server_ms - time.time() * 1000
        if abs(sample) > self._MAX_CLOCK_SKEW_MS:
            logger.warning(f"忽略异常的服务器时钟样本: {sample:.0f}ms")
            return
        if self._clock_samples == 0:
            self._clock_offset_ms = sample
        else:
            alpha = self._CLOCK_SMOOTHING
            self._clock_offset_ms = (1 - alpha) * self._clock_offset_ms + alpha * sample
        self._clock_samples += 1

    @staticmethod
    def _payload_server_time_ms(payload: dict[str, Any]) -> int | None:
        """优先取响应体里的 server_time，支持顶层或 data 内嵌、秒级自动升级。"""
        value = payload.get("server_time")
        if value is None:
            data = payload.get("data")
            value = data.get("server_time") if isinstance(data, dict) else None
        ms = epoch_millis(value)
        return int(ms) if ms is not None else None

    @staticmethod
    def parse_cookie_string(raw: str) -> dict[str, str]:
        """解析浏览器 cookie 字符串为字典。"""
        return BasePlatformParser.parse_cookie_string(raw)

    @staticmethod
    def _parse_exercise_response(q_resp: dict[str, Any]) -> list[dict[str, Any]]:
        """解析习题 API 响应中的题目列表，兼容多种响应结构。"""
        return BasePlatformParser.parse_exercise_response(q_resp)

    # 平台抽象方法

    async def login(
        self,
        cookies: dict[str, str] | None = None,
        display: str = "image",
        open_browser: bool = False,
        timeout: int = 120,
    ) -> UserInfo:
        """执行登录并返回用户信息。"""
        if cookies is not None:
            return await self._login_with_cookies(cookies)
        return await self._login_with_qrcode(display=display, open_browser=open_browser, timeout=timeout)

    @abstractmethod
    async def _login_with_cookies(self, cookies: dict[str, str]) -> UserInfo: ...

    # 扫码登录协议差异点：平台只需实现下列钩子，轮询与收尾由基类统一完成。

    qr_platform_name: ClassVar[str] = "雨课堂"

    def _qr_ws_url(self) -> str:
        return "wss://www.yuketang.cn/wsapp/"

    def _qr_request_payload(self) -> dict[str, Any]:
        return {"op": "requestlogin", "role": "web", "version": 1.4, "type": "qrcode"}

    def _qr_expire_seconds(self, message: dict[str, Any]) -> float | None:
        """消息中携带的下次轮询超时提示；无提示时返回 None。"""
        return None

    async def _qr_publish_image(self, message: dict[str, Any], qr_session: QrCodeSession, expires_in: float) -> None:
        """把本轮消息中的二维码展示给用户；默认无图像载荷。"""
        return None

    @abstractmethod
    async def _qr_exchange_cookies(self, login_data: dict[str, Any]) -> dict[str, str]:
        """用扫码回执换取会话 Cookie。"""

    async def _qr_wait_for_scan(self, qr_session: QrCodeSession, timeout: int) -> dict[str, Any]:
        """通用扫码轮询：连接 WebSocket，推送二维码并等待登录成功回执。"""
        payload = json.dumps(self._qr_request_payload())
        wait_limit = 60.0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        async with build_session(timeout=max(30.0, timeout + 15.0), proxy=None, user_agent=None) as ws_sess:
            async with ws_sess.ws_connect(self._qr_ws_url(), timeout=max(30.0, timeout + 15.0)) as ws:
                await ws.send_str(payload)
                while loop.time() < deadline:
                    remain_time = max(1.0, deadline - loop.time())
                    try:
                        raw = await asyncio.wait_for(ws.recv_str(), timeout=min(wait_limit, remain_time))
                    except TimeoutError:
                        if loop.time() >= deadline:
                            break
                        await ws.send_str(payload)
                        continue
                    except Exception as exc:  # 连接被对端关闭时 curl 会抛传输异常
                        raise AuthError(f"WebSocket 连接已关闭: {exc}") from exc
                    if not raw:
                        raise AuthError("WebSocket 连接已关闭")
                    message = json.loads(raw)
                    hint = self._qr_expire_seconds(message)
                    if hint is not None:
                        wait_limit = hint
                    await self._qr_publish_image(message, qr_session, remain_time)
                    if message.get("op") == "loginsuccess":
                        return message
        raise AuthError("扫码超时或未获得有效认证回执")

    async def _login_with_qrcode(
        self,
        display: str = "image",
        open_browser: bool = False,
        timeout: int = 120,
    ) -> UserInfo:
        """扫码登录通用流程：展示二维码 → 等待回执 → 换取 Cookie → 校验身份。"""
        qr_session = QrCodeSession(display=display, open_browser=open_browser, platform_name=self.qr_platform_name)
        await qr_session.start()
        try:
            login_data = await self._qr_wait_for_scan(qr_session, timeout)
            cookies = await self._qr_exchange_cookies(login_data)
            user = await self._login_with_cookies(cookies)
            qr_session.mark_success(name=user.name)
            if qr_session.display == "browser":
                await asyncio.sleep(1.2)
            return user
        except AuthError:
            raise
        except Exception as e:
            raise AuthError(f"扫码通信失败: {e}") from e
        finally:
            await qr_session.close()

    @abstractmethod
    async def get_courses(self) -> list[Course]: ...

    @abstractmethod
    async def _get_chapter_data(self, course: Course) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def _get_leaf_schedules(self, course: Course) -> dict[int, float]: ...

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

    def _format_web_task_url(self, course: Course, leaf_id: str | int, task_type: str = "exercise") -> str | None:
        """格式化官方平台的前端网页直达 URL，方便用户/Agent 在风控拦截时直接打开。"""
        cid = self._classroom_id(course)
        lid = str(leaf_id)
        if "xuetangx" in self.api_base:
            sign = str(course.metadata.get("sign") or "")
            return f"https://www.xuetangx.com/learn/{sign}/{course.id}/{cid}/{task_type}/{lid}"
        if "yuketang" in self.api_base:
            return f"https://www.yuketang.cn/v2/web/studentLog/{cid}/{lid}"
        return None

    def _get_request_kwargs(self, course: Course) -> dict[str, Any] | None:
        return None

    async def _get_completed_video_ids(self, course: Course) -> set[int] | None:
        """返回平台权威的「已完成视频 ID」集合。

        返回 `None` 表示平台没有更可靠的来源，基类退回用 `_get_leaf_schedules`
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

    async def get_videos(self, course: Course) -> dict[int, str]:
        """从章节数据构建视频字典。"""
        data = await self._get_chapter_data(course)
        # 优先使用平台权威的「已完成视频集合」；没有时退回章节进度值。
        authoritative_completed = await self._get_completed_video_ids(course)
        schedules = {} if authoritative_completed is not None else await self._get_leaf_schedules(course)
        videos: dict[int, str] = {}
        completed_video_ids: set[int] = set()
        for leaf, chapter_name, section_name in BasePlatformParser.iter_leaves_with_context(data):
            if leaf.get("leaf_type") not in (0, 1, 2, 8) or not leaf.get("is_show", True):
                continue
            leaf_id = leaf.get("id")
            if leaf_id is None:
                continue
            video_id = int(leaf_id)
            is_completed = (
                video_id in authoritative_completed if authoritative_completed is not None else schedules.get(video_id, 0.0) >= 1.0
            )
            if is_completed:
                completed_video_ids.add(video_id)

            label = BasePlatformParser.format_leaf_label(leaf, chapter_name, section_name)
            if is_completed:
                label = f"{label} [已完成]"
            videos[video_id] = label

        course.metadata["completed_video_ids"] = completed_video_ids
        return videos

    async def get_homeworks(self, course: Course) -> list[Homework]:
        """获取课程作业列表。

        截止时间直接取自章节树（`score_deadline`/`end_time`）；逐个叶子请求
        leaf_info 既拿不到更准确的值，还会产生 N 次多余往返。
        """
        data = await self._get_chapter_data(course)
        schedules = await self._get_leaf_schedules(course)
        homeworks: list[Homework] = []
        for leaf, chapter_name, section_name in BasePlatformParser.iter_leaves_with_context(data):
            # leaf_type 语义以平台前端为准：6=作业；5=考试、7=课件、9=随堂测验、10=公告均不属作业
            if leaf.get("leaf_type") != 6 or not leaf.get("is_show", True):
                continue
            leaf_id = leaf.get("id")
            if leaf_id is None:
                continue
            is_completed = schedules.get(int(leaf_id), 0.0) >= 1.0
            homeworks.append(
                Homework(
                    id=str(leaf_id),
                    name=BasePlatformParser.format_leaf_label(leaf, chapter_name, section_name),
                    deadline=leaf.get("score_deadline") or leaf.get("end_time") or None,
                    completed=is_completed,
                    metadata={"is_completed": is_completed},
                )
            )
        return homeworks

    async def get_exams(self, course: Course) -> list[ExamItem]:
        """获取课程的期中/期末考试列表。"""
        data = await self._get_chapter_data(course)
        schedules = await self._get_leaf_schedules(course)
        exams: list[ExamItem] = []
        for leaf, chapter_name, section_name in BasePlatformParser.iter_leaves_with_context(data):
            # 只有 leaf_type=5 才进入考场子系统；9=随堂测验属于活动流，不走考场接口
            if leaf.get("leaf_type") != 5 or not leaf.get("is_show", True):
                continue
            leaf_id = leaf.get("id")
            if leaf_id is None:
                continue
            is_completed = schedules.get(int(leaf_id), 0.0) >= 1.0
            deadline = epoch_millis(leaf.get("score_deadline") or leaf.get("end_time"))
            label = BasePlatformParser.format_leaf_label(leaf, chapter_name, section_name)
            exams.append(
                ExamItem(
                    id=str(leaf_id),
                    name=label,
                    start_time=epoch_millis(leaf.get("start_time")),
                    deadline=deadline,
                    completed=is_completed,
                )
            )

        for exam in exams:
            try:
                cover = await self.get_exam_cover(exam.id, course)
            except Exception as exc:  # 单个考试封面失败不影响整体列表
                logger.debug(f"考试 {exam.id} 封面查询失败: {exc}")
                continue
            exam.status = cover.status
            if cover.status == "已交卷":
                exam.completed = True
            elif cover.status == "已缺考":
                exam.overdue = True
        return exams

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

    def bind_exam_room(self, host: str, eid: int, login_url: str) -> None:
        """绑定已存在的考场会话，避免重复换票把浏览器端挤下线。"""
        self._exam_room_binding = (host, eid, login_url)

    async def _open_exam_room(self, leaf_id: str | int, course: Course) -> tuple[str, int, str]:
        """返回 (exam_host, exam_id, 考场直达 URL)；已绑定会话时不再换票。"""
        binding = getattr(self, "_exam_room_binding", None)
        if binding is not None:
            return binding
        return await self._mint_exam_room(leaf_id, course)

    async def _mint_exam_room(self, leaf_id: str | int, course: Course) -> tuple[str, int, str]:
        """换票建立新的考场会话；子类实现。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现考场令牌接口")

    @staticmethod
    def _exam_status_text(raw: Any) -> str:
        """考场 result.status 归一：1 答题中，2-5 已交卷，0/6 未开始，其余原样。"""
        if raw in (2, 3, 4, 5):
            return "已交卷"
        if raw == 1:
            return "答题中"
        if raw in (0, 6):
            return "未开始"
        return str(raw) if raw is not None else "未知"

    async def get_exam_room_status(self, leaf_id: str | int, course: Course) -> str | None:
        """读取考场子系统权威状态；失败时返回 None。"""
        try:
            host, eid, _login_url = await self._open_exam_room(leaf_id, course)
            resp = await self._request_json("GET", f"{host}/exam_room/cover", params={"exam_id": eid})
        except (APIError, AuthError, RiskControlError, NotImplementedError):
            return None
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        if "status" not in result:
            return None
        return self._exam_status_text(result.get("status"))

    async def get_exam_status_snapshot(self, leaf_id: str | int, course: Course) -> dict[str, Any]:
        """只读汇总考场各接口状态，用于定位「页面已提交但成绩未落库」。"""
        host, eid, _login_url = await self._open_exam_room(leaf_id, course)
        snapshot: dict[str, Any] = {"exam_id": str(leaf_id)}

        cover = await self._request_json("GET", f"{host}/exam_room/cover", params={"exam_id": eid})
        cover_data = cover.get("data") if isinstance(cover.get("data"), dict) else {}
        result = cover_data.get("result") if isinstance(cover_data.get("result"), dict) else {}
        snapshot["room_status"] = self._exam_status_text(result.get("status"))
        snapshot["room_status_raw"] = result.get("status") if isinstance(result.get("status"), int) else None
        snapshot["result_detail"] = result
        try:
            lms_cover = await self.get_exam_cover(leaf_id, course)
            snapshot["lms_status"] = lms_cover.raw_status
            snapshot["lms_status_text"] = lms_cover.status
        except Exception:
            snapshot["lms_status"] = None

        refresh = await self._request_json("GET", f"{host}/exam_room/refresh_time", params={"exam_id": eid})
        snapshot["refresh_code"] = refresh.get("errcode")
        snapshot["refresh_message"] = str(refresh.get("errmsg") or "")

        cached = await self._request_json("GET", f"{host}/exam_room/cache_results", params={"exam_id": eid})
        cached_data = cached.get("data") if isinstance(cached.get("data"), dict) else {}
        results = cached_data.get("results") if isinstance(cached_data.get("results"), list) else []
        snapshot["cached_count"] = len(results)
        snapshot["cached_sample"] = results[0] if results and isinstance(results[0], dict) else None

        problem = await self._request_json("GET", f"{host}/exam_room/problem_results", params={"exam_id": eid})
        snapshot["result_code"] = problem.get("errcode")
        snapshot["result_message"] = str(problem.get("errmsg") or "")
        return snapshot

    async def get_exam_result(self, leaf_id: str | int, course: Course) -> dict[str, Any]:
        """读取考场成绩与作答结果（只读）；未交卷时平台返回空数据。"""
        host, eid, _login_url = await self._open_exam_room(leaf_id, course)
        resp = await self._request_json("GET", f"{host}/exam_room/problem_results", params={"exam_id": eid})
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        payload = dict(data) if isinstance(data, dict) else {}
        payload["errcode"] = resp.get("errcode")
        payload["errmsg"] = resp.get("errmsg")
        return payload

    async def begin_exam(self, leaf_id: str | int, course: Course) -> ExamStartResult:
        """真正开始作答：调用 `start_paper` 创建作答记录并启动服务端计时。"""
        host, eid, login_url = await self._open_exam_room(leaf_id, course)
        resp = await self._request_json("POST", f"{host}/exam_room/start_paper", json={"exam_id": eid})
        ok = int(resp.get("errcode") or 0) == 0
        cover = await self.get_exam_cover(eid, course)
        message = "作答已开始，服务端计时已启动" if ok else f"开始作答失败: {resp.get('errmsg') or '未知错误'}"
        return ExamStartResult(
            course_id=str(course.id),
            exam_id=str(leaf_id),
            title=cover.title,
            started=ok,
            limit_minutes=cover.limit_minutes,
            web_url=login_url,
            message=message,
        )

    async def get_exam_paper(self, leaf_id: str | int, course: Course) -> ExamPaperData:
        """读取考场子系统下发的整卷题目；风控拦截时拉起浏览器由用户完成滑块后截获。"""
        host, eid, _login_url = await self._open_exam_room(leaf_id, course)
        try:
            payload = await self._request_json("GET", f"{host}/exam_room/show_paper", params={"exam_id": eid})
        except RiskControlError:
            from wkhelper.core.browser_cdp import find_chromium_browser, solve_captcha_interactive

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

        paper = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        raw_problems = paper.get("problems") if isinstance(paper.get("problems"), list) else []
        problems = [self._parse_exam_problem(item, index) for index, item in enumerate(raw_problems, 1)]
        cover = await self.get_exam_cover(eid, course)
        return ExamPaperData(
            course_id=str(course.id),
            exam_id=str(leaf_id),
            title=str(paper.get("title") or cover.title),
            problems=problems,
        )

    @staticmethod
    def _parse_exam_problem(item: Any, index: int) -> ExamProblemItem:
        """把考场试卷的题目条目归一为对外模型。"""
        raw = item if isinstance(item, dict) else {}
        raw_options = raw.get("Options") if isinstance(raw.get("Options"), list) else []
        options = [
            OptionItem(key=str(opt.get("key") or ""), text=html_to_md(str(opt.get("value") or "")))
            for opt in raw_options
            if isinstance(opt, dict)
        ]
        raw_answer = raw.get("Answer")
        answers = [str(a) for a in raw_answer] if isinstance(raw_answer, list) else []
        type_text = str(raw.get("TypeText") or raw.get("Type") or "")
        prompt, images = html_to_md_with_images(str(raw.get("Body") or ""))
        return ExamProblemItem(
            index=index,
            id=str(raw.get("problem_id") or index),
            type=type_text or None,
            prompt=prompt,
            images=images,
            options=options,
            score=float(raw.get("score") or raw.get("Score") or 0.0),
            answer=answers,
            has_remark=bool(raw.get("HasRemark")),
        )

    async def get_exam_answer_state(self, leaf_id: str | int, course: Course) -> tuple[list[str], list[str]]:
        """对照试卷与暂存作答，返回 (已作答题目 ID, 全部题目 ID)。"""
        host, eid, _login_url = await self._open_exam_room(leaf_id, course)
        cached = await self._request_json("GET", f"{host}/exam_room/cache_results", params={"exam_id": eid})
        cached_data = cached.get("data") if isinstance(cached.get("data"), dict) else {}
        results = cached_data.get("results") if isinstance(cached_data.get("results"), list) else []
        answered_ids = {str(item.get("problem_id")) for item in results if isinstance(item, dict) and item.get("result")}

        paper = await self.get_exam_paper(leaf_id, course)
        all_ids = [problem.id for problem in paper.problems]
        answered = [pid for pid in all_ids if pid in answered_ids]
        return answered, all_ids

    async def answer_exam_problem(
        self,
        leaf_id: str | int,
        course: Course,
        problem_id: str,
        answer: list[str],
    ) -> ExamAnswerResult:
        """提交考试单题答案，答案立即写入服务端暂存。"""
        host, eid, _login_url = await self._open_exam_room(leaf_id, course)
        payload = {
            "results": [{"problem_id": int(problem_id), "result": answer, "time": self._server_now_ms()}],
            "exam_id": eid,
            "record": [],
        }
        resp = await self._request_json("POST", f"{host}/exam_room/answer_problem", json=payload)
        ok = int(resp.get("errcode") or 0) == 0
        message = str(resp.get("errmsg") or ("作答已保存" if ok else "作答保存失败"))
        return ExamAnswerResult(
            dry_run=False,
            exam_id=str(leaf_id),
            problem_id=str(problem_id),
            answer=answer,
            saved=ok,
            message=message,
        )

    async def submit_exam(self, leaf_id: str | int, course: Course) -> ExamSubmitResult:
        """交卷：以服务端暂存作答为准提交整卷。"""
        host, eid, _login_url = await self._open_exam_room(leaf_id, course)
        cached = await self._request_json("GET", f"{host}/exam_room/cache_results", params={"exam_id": eid})
        cached_data = cached.get("data") if isinstance(cached.get("data"), dict) else {}
        results = cached_data.get("results") if isinstance(cached_data.get("results"), list) else []
        answered, all_ids = await self.get_exam_answer_state(leaf_id, course)
        unanswered = [pid for pid in all_ids if pid not in set(answered)]

        resp = await self._request_json("POST", f"{host}/exam_room/submit_paper", json={"results": results, "exam_id": eid})
        code = int(resp.get("errcode") or 0)
        ok = code == 0
        message = str(resp.get("errmsg") or ("交卷成功" if ok else "交卷失败"))

        verified = False
        if ok:
            check = await self._request_json("GET", f"{host}/exam_room/cover", params={"exam_id": eid})
            check_data = check.get("data") if isinstance(check.get("data"), dict) else {}
            check_result = check_data.get("result") if isinstance(check_data.get("result"), dict) else {}
            verified = check_result.get("status") in (2, 3, 4, 5)
            if not verified:
                message = "平台返回成功但考场状态未变为已交卷，请在浏览器考场页提交"
        elif code != 0:
            message = f"{message} (errcode: {code})"

        return ExamSubmitResult(
            dry_run=False,
            exam_id=str(leaf_id),
            answered_count=len(answered),
            unanswered=unanswered,
            submitted=ok and verified,
            message=message,
        )

    async def get_articles(self, course: Course) -> list[ArticleItem]:
        """获取课程资料/图文节点列表。"""
        data = await self._get_chapter_data(course)
        schedules = await self._get_leaf_schedules(course)
        articles: list[ArticleItem] = []
        for leaf, chapter_name, section_name in BasePlatformParser.iter_leaves_with_context(data):
            if leaf.get("leaf_type") != 3 or not leaf.get("is_show", True):
                continue
            leaf_id = leaf.get("id")
            if leaf_id is None:
                continue
            label = BasePlatformParser.format_leaf_label(leaf, chapter_name, section_name)
            is_completed = schedules.get(int(leaf_id), 0.0) >= 1.0
            articles.append(ArticleItem(id=str(leaf_id), name=label, completed=is_completed))
        return articles

    async def _mark_article_finish(self, leaf_id: str | int, course: Course) -> bool:
        """标记资料节点已完成；平台差异仅在查询参数，由 `_article_finish_params` 提供。"""
        url = f"{self.api_base}/{self.api_prefix}/v1/lms/learn/user_article_finish/{leaf_id}/"
        kwargs = self._get_request_kwargs(course) or {}
        resp = await self._request_json("GET", url, params=self._article_finish_params(course), **kwargs)
        return bool(resp.get("success", False))

    def _article_finish_params(self, course: Course) -> dict[str, Any] | None:
        return None

    async def read_article(self, leaf_id: str | int, course: Course) -> ArticleReadResult:
        """标记指定资料/图文节点为已读已完成。"""
        articles = await self.get_articles(course)
        target = next((item for item in articles if item.id == str(leaf_id)), None)
        title = target.name if target else str(leaf_id)
        success = await self._mark_article_finish(leaf_id, course)
        return ArticleReadResult(
            course_id=str(course.id),
            leaf_id=str(leaf_id),
            name=title,
            success=success,
            completed=True,
        )

    async def get_discussions(self, course: Course) -> list[DiscussionItem]:
        """获取课程讨论题列表。

        章节进度（`leaf_schedules`）与讨论页徽标（`comment/status`）是两个数据源，
        列表必须与徽标一致，否则已完成讨论会被反复要求发言。
        """
        data = await self._get_chapter_data(course)
        schedules = await self._get_leaf_schedules(course)
        discussions: list[DiscussionItem] = []
        for leaf, chapter_name, section_name in BasePlatformParser.iter_leaves_with_context(data):
            if leaf.get("leaf_type") not in (4, 10) or not leaf.get("is_show", True):
                continue
            leaf_id = leaf.get("id")
            if leaf_id is None:
                continue
            label = BasePlatformParser.format_leaf_label(leaf, chapter_name, section_name)
            is_completed = schedules.get(int(leaf_id), 0.0) >= 1.0
            discussions.append(DiscussionItem(id=str(leaf_id), name=label, completed=is_completed))

        pending = [item for item in discussions if not item.completed]
        if pending:
            semaphore = asyncio.Semaphore(MAX_WORKERS_STATUS)

            async def _status(item: DiscussionItem) -> bool | None:
                async with semaphore:
                    return await self._discussion_leaf_completed(item.id, course)

            statuses = await asyncio.gather(*(_status(item) for item in pending), return_exceptions=True)
            for item, status in zip(pending, statuses, strict=True):
                if isinstance(status, BaseException):
                    logger.debug(f"讨论 {item.id} 发言状态查询失败，回退章节进度: {status}")
                elif status is True:
                    item.completed = True
        return discussions

    async def _discussion_leaf_completed(self, leaf_id: str | int, course: Course) -> bool | None:
        """按平台发言状态判定讨论节点是否完成；无法廉价判定时返回 None 回退章节进度。"""
        return None

    async def _fetch_discussion_unit(self, leaf_id: str | int, course: Course) -> dict[str, Any]:
        """拉取讨论题单元信息；两个平台的讨论接口族完全不同，由平台实现。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现讨论题单元接口")

    async def _discussion_completed(self, topic_info: dict[str, Any], course: Course, leaf_id: str | int) -> bool:
        """讨论题是否已打卡；默认按本人评论数判断。"""
        return int(topic_info.get("user_comment_num") or 0) > 0

    async def get_discussion_detail(self, leaf_id: str | int, course: Course) -> DiscussionTopicDetail:
        """获取讨论题详情与打卡状态。"""
        topic_info = await self._fetch_discussion_unit(leaf_id, course)
        topic_id = int(topic_info["id"])
        user_comment_num = int(topic_info.get("user_comment_num") or 0)
        comment_count = int(topic_info.get("commented") or 0)
        content = topic_info.get("content")
        raw_text = str(content.get("text") or "") if isinstance(content, dict) else ""
        completed = await self._discussion_completed(topic_info, course, leaf_id)

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

    async def submit_discussion(self, leaf_id: str | int, course: Course, content: str) -> DiscussionCommentResult:
        """提交讨论题发言并返回打卡结果。"""
        topic_info = await self._fetch_discussion_unit(leaf_id, course)
        topic_id = int(topic_info["id"])
        parsed = await self._post_discussion_comment(topic_info, content, course, leaf_id)
        data = parsed.get("data")
        nested = data.get("data") if isinstance(data, dict) else None
        msg = parsed.get("msg") or (data.get("message") if isinstance(data, dict) else None) or "操作成功"
        comment_id = nested.get("id") if isinstance(nested, dict) else None
        return DiscussionCommentResult(
            course_id=str(course.id),
            leaf_id=str(leaf_id),
            topic_id=topic_id,
            comment_id=int(comment_id) if comment_id else None,
            success=bool(parsed.get("success", False)),
            message=str(msg),
        )

    async def _post_discussion_comment(
        self,
        topic_info: dict[str, Any],
        content: str,
        course: Course,
        leaf_id: str | int,
    ) -> dict[str, Any]:
        """发送讨论发言，返回平台原始响应。"""
        raise NotImplementedError(f"{type(self).__name__} 未实现讨论发言接口")

    async def get_leaf_questions(self, leaf_id: str | int, course: Course) -> list[dict[str, Any]]:
        """获取指定叶子节点的习题列表。"""
        url = self._build_leaf_info_url(leaf_id, course)
        params = self._get_leaf_info_params(leaf_id, course)
        kwargs = self._get_request_kwargs(course) or {}
        resp = await self._request_json("GET", url, params=params, **kwargs)
        data = resp.get("data")
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

        ex_url = self._build_exercise_list_url(leaf_type_id, sku_id)
        try:
            q_resp = await self._request_json("GET", ex_url, **kwargs)
        except RiskControlError as exc:
            web_url = self._format_web_task_url(course, leaf_id, "exercise")
            from wkhelper.core.browser_cdp import find_chromium_browser, solve_captcha_interactive

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
        questions = self._parse_exercise_response(q_resp)
        q_data = q_resp.get("data")
        if isinstance(q_data, dict):
            font_url = q_data.get("font")
            if font_url and questions:
                try:
                    from wkhelper.core.deobfuscator import deobfuscate_questions

                    await deobfuscate_questions(questions, font_url, self.fetch_bytes)
                except Exception as e:
                    logger.warning(f"题目反混淆失败，返回原文: {e}")
        return questions

    async def _submit_answer(
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
            req_start = time.perf_counter()
            response = await self.client.post(url, json=payload, **(kwargs or {}))
            body = response.text or ""
            self._trace_http("POST", url, req_start, response.status_code)

            hint = parse_retry_hint(body, response.headers.get("Retry-After"))
            if hint is not None or response.status_code == 429:
                wait = hint if hint is not None else self.gate.backoff_delay(attempt, self.retry_policy)
                if on_status:
                    on_status(f"限流等待 {wait:.0f}s")
                await self.gate.cooldown(wait)
                continue

            try:
                response.raise_for_status()
            except HttpError as exc:
                if response.status_code in (401, 403):
                    raise AuthError(f"平台拒绝提交（HTTP {response.status_code}），登录态可能已失效") from exc
                if response.status_code >= 500 and attempt < max_attempts:
                    await asyncio.sleep(self.gate.backoff_delay(attempt, self.retry_policy))
                    continue
                raise APIError(
                    f"题目 {problem_id} 提交失败（HTTP {response.status_code}）",
                    status_code=response.status_code,
                ) from exc
            parsed = response.json()
            if not isinstance(parsed, dict):
                raise APIError(f"题目 {problem_id} 提交返回结构无法识别")
            return self._parse_submit_response(parsed)

        raise APIError(f"题目 {problem_id} 提交重试次数过多，平台持续限流", retryable=True)

    async def fetch_bytes(self, url: str) -> bytes:
        """下载远程资源字节。

        传输实现由平台层持有，`core` 只消费这一能力（见 `core.transport.Fetcher`），
        从而避免把具体 HTTP 客户端类型泄漏到导出与反混淆模块。
        """
        response = await self.client.get(url, timeout=15, headers={"Accept": "*/*"})
        response.raise_for_status()
        return bytes(response.content or b"")

    @abstractmethod
    async def _prepare_video_context(self, video_id: str, course: Course) -> VideoContext: ...

    @property
    @abstractmethod
    def heartbeat_profile(self) -> HeartbeatProfile:
        """本平台心跳报文的固定字段，缺失实现时启动即报错。"""

    def _build_heartbeat_payload(
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
    ) -> JsonDict:
        """构建单个心跳打点，字段语义对齐 Web 播放器（见前端 media-log 定义）。"""
        profile = self.heartbeat_profile
        nonce = _page_nonce()
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
            "ts": str(int(timestamp) + int(round(self._clock_offset_ms))),
            "u": int(user_id),
            "uip": "",
            "c": int(course_id),
            "v": _as_int_or_str(video_id),
            "skuid": _as_int_or_str(sku_id),
            "classroomid": str(classroom_id),
            "cc": cc,
            "d": round(float(duration), 1),
            "pg": f"{video_id}_{nonce}",
            "sq": seq,
            "t": "video",
            "cards_id": 0,
            "slide": 0,
            "v_url": "",
        }

    async def do_video(
        self,
        video_id: str,
        video_name: str,
        course: Course,
        options: VideoWatchOptions | None = None,
        on_event: EventCallback | None = None,
    ) -> VideoWatchResult:
        """观看单个视频。"""
        ctx = await self._prepare_video_context(video_id, course)
        opts = options or VideoWatchOptions()
        try:
            return await generic_watch_video(
                client=self.client,
                video_id=ctx.video_id,
                video_name=video_name,
                classroom_id=ctx.classroom_id,
                user_id=ctx.user_id,
                course_id=ctx.course_id,
                sku_id=ctx.sku_id,
                progress_url=ctx.progress_url,
                heartbeat_url=ctx.heartbeat_url,
                payload_gen=self._build_heartbeat_payload,
                progress_params=ctx.progress_params,
                request_kwargs=ctx.request_kwargs,
                options=opts,
                on_event=on_event,
                media_ccid=ctx.media_ccid,
                video_duration=ctx.duration,
            )
        except RiskControlError as exc:
            web_url = self._format_web_task_url(course, video_id, "video")
            next_actions = (
                [NextAction(reason="在浏览器打开视频页面完成滑块人机验证后重试", argv=list(sys.argv), description=web_url or "")]
                if web_url
                else None
            )
            raise RiskControlError(str(exc), web_url=web_url, next_actions=next_actions) from exc

    async def get_video_progress(self, video_id: str, course: Course) -> VideoProgress:
        """查询单视频服务端进度与完成标识。"""
        vctx = await self._prepare_video_context(video_id, course)
        resp = await self.client.get(
            vctx.progress_url,
            params=vctx.progress_params,
            **(vctx.request_kwargs or {}),
        )
        resp.raise_for_status()
        text = resp.text or ""
        rate, _ = parse_progress_text(text, str(video_id))
        return VideoProgress(
            course_id=str(course.id),
            video_id=str(video_id),
            current_rate=rate,
            completed='"completed":1' in text or rate >= 1.0,
        )

    @abstractmethod
    async def _prepare_submit_context(self, homework: Homework, course: Course) -> dict[str, Any]: ...

    async def do_homework(
        self,
        homework: Homework,
        course: Course,
        answers: dict[str, list[str]] | None = None,
        on_event: EventCallback | None = None,
        concurrency: int = 1,
        delay: float = 0.0,
        delay_jitter: float = 0.0,
        random_order: bool = False,
    ) -> HomeworkSubmitResult:
        """完成单个作业，提交显式答案集合并返回结构化结果。"""
        questions = await self.get_leaf_questions(homework.id, course)
        submit_ctx = await self._prepare_submit_context(homework, course)
        kwargs = self._get_request_kwargs(course)

        if answers is None:
            raise ValueError("必须显式提供作业答案集合以供提交")

        resolved_pairs: list[tuple[dict[str, Any], list[str]]] = []
        for q in questions:
            pid = str(q.get("problem_id") or q.get("id"))
            if pid in answers:
                resolved_pairs.append((q, answers[pid]))

        async def submit_func(
            problem_id: int,
            answer: list[str],
            course_info: Course,
            client: AsyncSession,
            kwargs: dict[str, Any] | None,
            question: dict[str, Any] | None = None,
        ) -> QuestionSubmitOutcome:
            return await self._submit_answer(
                homework.name,
                problem_id,
                answer,
                kwargs=kwargs,
                submit_ctx=submit_ctx,
                question=question,
            )

        return await generic_submit_homework(
            resolved_pairs=resolved_pairs,
            submit_func=submit_func,
            course_info=course,
            client=self.client,
            headers=kwargs,
            on_event=on_event,
            concurrency=concurrency,
            delay=delay,
            delay_jitter=delay_jitter,
            random_order=random_order,
        )

    async def submit_question(
        self,
        homework: Homework,
        course: Course,
        question_id: str | int,
        answer: list[str] | str,
    ) -> QuestionSubmitResult:
        """提交单个题目的答案并返回结果。"""
        submit_ctx = await self._prepare_submit_context(homework, course)
        kwargs = self._get_request_kwargs(course)
        ans_list = [answer] if isinstance(answer, str) else list(answer)
        try:
            pid = int(question_id)
        except (ValueError, TypeError):
            raise ValueError(f"题目 ID 必须为有效整数: {question_id}") from None

        # 取题目原始结构：填空题必须按题型改用 answers 映射提交
        question: dict[str, Any] | None = None
        try:
            questions = await self.get_leaf_questions(homework.id, course)
        except (APIError, AuthError) as exc:
            logger.debug(f"拉取题目结构失败，按默认题型提交: {exc}")
            questions = []
        for item in questions:
            if str(item.get("problem_id") or item.get("id")) == str(pid):
                question = item
                break

        try:
            res = await self._submit_answer(
                homework.name,
                pid,
                ans_list,
                kwargs=kwargs,
                submit_ctx=submit_ctx,
                question=question,
            )
        except RiskControlError as exc:
            web_url = self._format_web_task_url(course, homework.id, "exercise")
            next_actions = (
                [NextAction(reason="在浏览器打开作业页面完成滑块人机验证后重试", argv=list(sys.argv), description=web_url or "")]
                if web_url
                else None
            )
            raise RiskControlError(str(exc), web_url=web_url, next_actions=next_actions) from exc
        return QuestionSubmitResult(
            question_id=str(question_id),
            submitted_answer=ans_list,
            success=res.success,
            is_correct=res.is_correct,
            correct_answer=res.correct_answer,
            error=res.error,
        )
