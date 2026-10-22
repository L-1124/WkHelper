"""CLI 共享依赖项与通用辅助函数。"""

import json
import logging
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from wkhelper.auth.cookies import parse_cookie_string
from wkhelper.cli.context import ctx
from wkhelper.domain.courses import Course, course_reference, dedupe_courses
from wkhelper.domain.errors import AuthError
from wkhelper.domain.json_types import JsonValue, json_str_list, parse_json_text
from wkhelper.domain.pagination import PaginationMeta
from wkhelper.domain.validation import validate_input, validation_message
from wkhelper.infra.credentials import CredentialStore
from wkhelper.infra.http import AsyncSession, build_session
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.xuetangx import XuetangXPlatform
from wkhelper.platform.yuketang import YuketangPlatform
from wkhelper.video.plan import VideoPlan

logger = logging.getLogger("wkhelper")
_COOKIE_ADAPTER = TypeAdapter(dict[str, str])


def _create_http_session(timeout: float = 15.0, impersonate: str | None = None) -> AsyncSession:
    """按全局网络上下文与目标平台创建 HTTP 会话。

    指纹伪装由平台声明（`BasePlatform.impersonate`）：会话 Cookie 与签发时的
    TLS 指纹绑定，平台间不一致时必须分别处理，否则会被直接拒绝。
    """
    return build_session(timeout=timeout, proxy=ctx.proxy, user_agent=ctx.user_agent, impersonate=impersonate)


def _read_json_file_or_stdin(file_path: str) -> JsonValue:
    """读取 JSON 文件；`-` 表示从 stdin 读取。"""
    if file_path == "-":
        content = sys.stdin.read()
    else:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"找不到输入文件: {file_path}")
        content = path.read_text(encoding="utf-8")
    try:
        return parse_json_text(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"输入文件不是合法的 JSON: {exc}") from exc


def _parse_cookie_payload(raw: JsonValue) -> dict[str, str]:
    """保留 Cookie 字符串语法，对对象执行严格类型校验。"""
    if isinstance(raw, str):
        return parse_cookie_string(raw)
    try:
        return _COOKIE_ADAPTER.validate_python(raw, strict=True)
    except ValidationError as exc:
        raise ValueError(validation_message(exc)) from None


def _parse_answers_payload(raw: JsonValue) -> dict[str, list[str]]:
    """解析答案载荷，兼容扁平映射与显式 answers 列表两种格式。"""
    if not isinstance(raw, dict):
        raise ValueError("答案内容必须为 JSON 对象")

    nested = raw.get("answers")
    if isinstance(nested, list):
        answers: dict[str, list[str]] = {}
        for index, item in enumerate(nested, 1):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index} 条答案必须为 JSON 对象")
            question_id = item.get("question_id")
            if not isinstance(question_id, str) or not question_id:
                raise ValueError(f"第 {index} 条答案缺少字符串字段 question_id")
            answer_value = item.get("answer")
            if isinstance(answer_value, str):
                answers[question_id] = [answer_value]
            elif isinstance(answer_value, list):
                answers[question_id] = json_str_list(answer_value)
            else:
                raise ValueError(f"第 {index} 条答案的 answer 必须为字符串或字符串数组")
        return answers

    flat: dict[str, list[str]] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            flat[str(key)] = [value]
        elif isinstance(value, list):
            flat[str(key)] = json_str_list(value)
        else:
            raise ValueError(f"题目 '{key}' 的答案必须为字符串或字符串数组")
    return flat


def _paginate[T](items: Sequence[T], limit: int, cursor: str | None) -> tuple[list[T], PaginationMeta]:
    """本地分页：稳定顺序切片并生成分页元数据。"""
    if limit <= 0:
        raise ValueError(f"limit 参数必须为正整数 (1-100)，当前提供: {limit}")
    bounded_limit = min(100, limit)
    offset = 0
    if cursor:
        try:
            offset = max(0, int(cursor))
        except ValueError:
            offset = 0
    end = min(len(items), offset + bounded_limit)
    return list(items[offset:end]), PaginationMeta(
        limit=bounded_limit,
        offset=offset,
        total=len(items),
        has_more=end < len(items),
        next_cursor=str(end) if end < len(items) else None,
    )


def _validate_platform_code(platform: str | None) -> None:
    """校验平台代码（命令行由 `Literal` 兜底，此处守住计划文件等外部输入）。"""
    if platform is not None and platform not in ("ykt", "xtzx"):
        raise ValueError(f"不支持的平台代码: {platform}，仅支持 ykt 或 xtzx")


_PLATFORM_TYPES: dict[str, type[BasePlatform]] = {"ykt": YuketangPlatform, "xtzx": XuetangXPlatform}


def _create_platform_instance(platform_code: str, client: AsyncSession) -> BasePlatform:
    """按平台代码实例化平台对象。"""
    platform_cls = _PLATFORM_TYPES.get(platform_code)
    if platform_cls is None:
        raise ValueError(f"未知的平台代码: {platform_code}")
    return platform_cls(client)


def _load_plan(plan_file: str) -> VideoPlan:
    """加载视频学习计划文件。"""
    return validate_input(VideoPlan, _read_json_file_or_stdin(plan_file))


def _resolve_account_and_platform(
    store: CredentialStore, platform_code: str | None, account_id: str | None
) -> tuple[str, str, dict[str, str]]:
    """解析目标平台代码、账号 ID 与可用 Cookies。"""
    _validate_platform_code(platform_code)

    if ctx.cookie_file:
        if ctx.credential_mode != "session":
            raise ValueError("全局 `--cookie-file` 仅支持与 `--credential-mode session` 搭配使用")
        if not platform_code:
            raise ValueError("使用全局 `--cookie-file` 时必须显式提供 `--platform`")
        cookies = _parse_cookie_payload(_read_json_file_or_stdin(ctx.cookie_file))
        return platform_code, account_id or "session_user", cookies

    plat = platform_code
    if plat is None:
        accounts = store.list_accounts()
        if not accounts:
            raise AuthError("未找到任何已保存账号，请先执行 `wkhelper auth login` 登录")
        platforms = {account.platform for account in accounts}
        if len(platforms) != 1:
            raise ValueError("系统存在多个平台的账号，请显式提供 `--platform` 参数")
        plat = next(iter(platforms))

    accounts = store.list_accounts(plat)
    if not accounts:
        raise AuthError(f"平台 '{plat}' 未找到已保存账号，请先执行登录")

    acc = account_id
    if acc is None:
        if len(accounts) == 1:
            acc = accounts[0].user_id
        else:
            raise ValueError(f"平台 '{plat}' 存在多个账号，请显式提供 `--account` 参数")

    cookies = store.get_cookies(plat, acc)
    if not cookies:
        raise AuthError(f"账号 '{acc}' 的凭据已丢失或损坏，请重新登录")

    return plat, acc, cookies


@asynccontextmanager
async def _open_platform(
    platform: str | None = None,
    account: str | None = None,
    timeout: float = 15.0,
) -> AsyncIterator[BasePlatform]:
    """打开已认证的平台客户端上下文。"""
    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    platform_cls = _PLATFORM_TYPES.get(platform_code)
    impersonate = platform_cls.impersonate if platform_cls is not None else None
    async with _create_http_session(timeout=timeout, impersonate=impersonate) as client:
        plat = _create_platform_instance(platform_code, client)
        plat.platform_code = platform_code
        plat.account_id = _account_id
        await plat.login(cookies=cookies)
        yield plat


@asynccontextmanager
async def _open_course(
    course: str,
    platform: str | None = None,
    account: str | None = None,
    timeout: float = 15.0,
) -> AsyncIterator[tuple[BasePlatform, Course]]:
    """打开已认证平台并解析目标课程。"""
    async with _open_platform(platform, account, timeout) as plat:
        yield plat, await _load_course(plat, course)


async def _load_course(plat: BasePlatform, course_id: str) -> Course:
    """按课程引用解析课程。

    优先匹配 `course list` 返回的完整引用（原生 ID + 课堂 ID）；也接受原生课程 ID，
    但同一课程挂在多个课堂时拒绝猜测，要求使用完整引用。
    """
    courses = dedupe_courses(await plat.get_courses())

    exact = next((course for course in courses if course_reference(course) == course_id), None)
    if exact is not None:
        return exact

    native_matches = [course for course in courses if str(course.id) == course_id]
    if not native_matches:
        raise KeyError(f"未找到课程 ID: {course_id}")
    if len(native_matches) > 1:
        references = ", ".join(course_reference(course) for course in native_matches)
        raise ValueError(f"课程 '{course_id}' 存在多个课堂，请使用完整课程引用: {references}")
    return native_matches[0]


__all__ = [
    "_create_http_session",
    "_load_course",
    "_load_plan",
    "_open_course",
    "_open_platform",
    "_paginate",
    "_parse_answers_payload",
    "_parse_cookie_payload",
    "_read_json_file_or_stdin",
]
