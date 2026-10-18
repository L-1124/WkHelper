"""基于 Cyclopts 的现代类型驱动 CLI 应用"""

import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

import msgspec
import niquests
from cyclopts import App, Parameter

from wkhelper.cli.envelope import CommandResult, NextAction, create_success_envelope, print_json
from wkhelper.cli.spec import get_command_schema
from wkhelper.core.credential_store import CredentialStore
from wkhelper.core.exceptions import APIError, AuthError, ConfirmationRequiredError, PartialFailureError
from wkhelper.core.exporter import export_questions_to_markdown, html_to_md
from wkhelper.core.models import (
    AccountListData,
    AuthStatusResult,
    Course,
    CourseDetail,
    CourseItem,
    CourseListData,
    ExecutionOverrides,
    ExecutionSettings,
    ExportResult,
    Homework,
    HomeworkItem,
    HomeworkListData,
    HomeworkOutline,
    HomeworkSubmitPreview,
    HomeworkSubmitResult,
    JsonDict,
    JsonValue,
    LoginResult,
    OptionItem,
    OutputFileResult,
    PaginationMeta,
    QuestionDetail,
    QuestionListData,
    QuestionOutline,
    QuestionSubmitPreview,
    QuestionSubmitResult,
    QuestionSummary,
    RemoveResult,
    StatusData,
    StorageStatus,
    VideoItem,
    VideoLearnPreview,
    VideoListData,
    VideoParameters,
    VideoPlan,
    VideoPlanSummary,
    VideoProgress,
    VideoTarget,
    course_reference,
    dedupe_courses,
    json_int,
    json_str_list,
    parse_json_text,
)
from wkhelper.core.runner import Runner
from wkhelper.core.video import VideoWatchOptions
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.xuetangx import XuetangXPlatform
from wkhelper.platform.yuketang import YuketangPlatform

logger = logging.getLogger("wkhelper")

VIDEO_STATUS_FILTERS: tuple[str, ...] = ("pending", "completed", "all")
CREDENTIAL_MODES: tuple[str, ...] = ("system", "session")
DISPLAY_MODES: tuple[str, ...] = ("image", "browser")
LOGIN_METHODS: tuple[str, ...] = ("qrcode", "cookie")


def unified_result_action(result: Any) -> int:
    """统一结果输出处理器：接管所有业务命令的强类型返回值，集中序列化为 JSON 信封并打印。"""
    if isinstance(result, CommandResult):
        print_json(create_success_envelope(data=result.data, meta=result.meta, next_actions=result.next_actions))
        return result.exit_code
    if isinstance(result, int):
        return result
    if result is not None:
        print_json(create_success_envelope(data=result))
    return 0


# 顶层 App
app = App(
    name="wkhelper",
    help="WkHelper: 面向 AI Agent 的网课自动化 CLI",
    help_formatter="plain",
    result_action=unified_result_action,
    exit_on_error=False,
    print_error=False,
)

# 各子命令 App
auth_app = app.command(App(name="auth", help="凭据与账号认证管理", help_formatter="plain", result_action=unified_result_action))
course_app = app.command(App(name="course", help="课程查询", help_formatter="plain", result_action=unified_result_action))
video_app = app.command(App(name="video", help="视频学习与查询", help_formatter="plain", result_action=unified_result_action))
homework_app = app.command(App(name="homework", help="作业查询与提交", help_formatter="plain", result_action=unified_result_action))
question_app = app.command(App(name="question", help="单题与按页题目检索", help_formatter="plain", result_action=unified_result_action))
questions_app = app.command(App(name="questions", help="课程题目导出", help_formatter="plain", result_action=unified_result_action))


class ExecutionContext:
    """进程级全局参数上下文"""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.format: str = "json"
        self.data_dir: str | None = None
        self.credential_mode: str = "system"
        self.cookie_file: str | None = None
        self.debug: bool = False

    def get_store(self) -> CredentialStore:
        return CredentialStore(data_dir=self.data_dir, credential_mode=self.credential_mode)


ctx = ExecutionContext()


def extract_global_flags(argv: list[str]) -> list[str]:
    """提取全局参数并重置更新 Context，返回剥离后的业务命令 tokens。"""
    ctx.reset()
    remaining: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--format" and i + 1 < len(argv):
            ctx.format = argv[i + 1]
            i += 2
        elif arg.startswith("--format="):
            ctx.format = arg.split("=", 1)[1]
            i += 1
        elif arg == "--data-dir" and i + 1 < len(argv):
            ctx.data_dir = argv[i + 1]
            i += 2
        elif arg.startswith("--data-dir="):
            ctx.data_dir = arg.split("=", 1)[1]
            i += 1
        elif arg == "--credential-mode" and i + 1 < len(argv):
            ctx.credential_mode = argv[i + 1]
            i += 2
        elif arg.startswith("--credential-mode="):
            ctx.credential_mode = arg.split("=", 1)[1]
            i += 1
        elif arg == "--cookie-file" and i + 1 < len(argv):
            ctx.cookie_file = argv[i + 1]
            i += 2
        elif arg.startswith("--cookie-file="):
            ctx.cookie_file = arg.split("=", 1)[1]
            i += 1
        elif arg == "--debug":
            ctx.debug = True
            i += 1
        else:
            remaining.append(arg)
            i += 1

    if ctx.credential_mode not in CREDENTIAL_MODES:
        raise ValueError(f"不支持的凭据模式: {ctx.credential_mode}，仅支持 system 或 session")
    return remaining


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
    """解析 Cookie 输入：浏览器 Cookie 字符串或 {名称: 字符串值} 对象。"""
    if isinstance(raw, str):
        return BasePlatform.parse_cookie_string(raw)
    if isinstance(raw, dict):
        cookies: dict[str, str] = {}
        for name, value in raw.items():
            if not isinstance(value, str):
                raise ValueError(f"Cookie '{name}' 的值必须为字符串")
            cookies[str(name)] = value
        return cookies
    raise ValueError("Cookie 输入必须为 Cookie 字符串或 {名称: 字符串值} 的 JSON 对象")


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


def _metadata_to_json(metadata: dict[str, Any]) -> JsonDict:
    """把平台元数据过滤为可序列化字段（跳过内部非 JSON 结构）。"""
    result: JsonDict = {}
    for key, value in metadata.items():
        try:
            result[str(key)] = msgspec.to_builtins(value)
        except TypeError:
            continue
    return result


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


def _validate_status_filter(status: str) -> None:
    if status not in VIDEO_STATUS_FILTERS:
        raise ValueError(f"不支持的 --status 取值: {status}，仅支持 {'/'.join(VIDEO_STATUS_FILTERS)}")


def _validate_platform_code(platform: str | None) -> None:
    if platform is not None and platform not in ("ykt", "xtzx"):
        raise ValueError(f"不支持的平台代码: {platform}，仅支持 ykt 或 xtzx")


def _resolve_account_and_platform(
    store: CredentialStore, platform_code: str | None, account_id: str | None
) -> tuple[str, str, dict[str, str]]:
    """解析平台、账号与 Cookie，缺省时按唯一性推断，绝不静默猜测。"""
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


def _create_platform_instance(platform_code: str, client: niquests.AsyncSession) -> BasePlatform:
    if platform_code == "ykt":
        return YuketangPlatform(client)
    if platform_code == "xtzx":
        return XuetangXPlatform(client)
    raise ValueError(f"未知的平台代码: {platform_code}")


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


def _load_plan(plan_file: str) -> VideoPlan:
    """读取并校验执行计划文件。"""
    return VideoPlan.from_mapping(_read_json_file_or_stdin(plan_file))


# 命令注册


@app.command(name="status")
async def status_cmd() -> CommandResult[StatusData]:
    """查看本地环境与账号概况。"""
    store = ctx.get_store()
    accounts = store.list_accounts()
    data = StatusData(
        credential_storage=store.get_storage_status(),
        accounts_count=len(accounts),
        accounts=accounts,
    )
    next_actions: list[NextAction] = []
    if not accounts and not ctx.cookie_file:
        next_actions.append(NextAction(reason="登录账号", argv=["wkhelper", "auth", "login", "--platform", "ykt", "--method", "qrcode"]))
    else:
        next_actions.append(NextAction(reason="查看课程列表", argv=["wkhelper", "course", "list"]))
    return CommandResult(data=data, next_actions=next_actions)


@app.command(name="schema")
async def schema_cmd(
    path: Annotated[str | None, Parameter(help="命令路径，例如 'video learn'")] = None,
    full: Annotated[bool, Parameter(name=["--full"], help="展开完整参数详情")] = False,
) -> CommandResult[JsonDict]:
    """CLI 契约自省。"""
    return CommandResult(data=get_command_schema(path, full=full))


# auth 命令群


@auth_app.command(name="list")
async def auth_list_cmd(
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
) -> CommandResult[AccountListData]:
    """列出已存储的账号摘要。"""
    _validate_platform_code(platform)
    store = ctx.get_store()
    return CommandResult(data=AccountListData(accounts=store.list_accounts(platform)))


@auth_app.command(name="status")
async def auth_status_cmd(
    platform: Annotated[str, Parameter(name=["--platform", "-p"], help="平台代码")],
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[AuthStatusResult]:
    """联网验证指定账号登录态。"""
    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        user = await plat.login(cookies=cookies)
        return CommandResult(data=AuthStatusResult(platform=platform_code, user_id=str(user.id), name=user.name, valid=True))


@auth_app.command(name="login")
async def auth_login_cmd(
    platform: Annotated[str, Parameter(name=["--platform", "-p"], help="平台代码")],
    method: Annotated[str, Parameter(name=["--method", "-m"], help="登录方式 (qrcode 或 cookie)")],
    cookie_file: Annotated[str | None, Parameter(name=["--cookie-file"], help="Cookie 文件路径")] = None,
    display: Annotated[str, Parameter(name=["--display"], help="二维码呈现方式 (image 或 browser)")] = "image",
    open_browser: Annotated[bool, Parameter(name=["--open-browser"], help="自动打开浏览器")] = False,
    timeout: Annotated[int, Parameter(name=["--timeout"], help="扫码超时秒数")] = 120,
) -> CommandResult[LoginResult]:
    """账号登录并安全持久化凭据。"""
    _validate_platform_code(platform)
    if method not in LOGIN_METHODS:
        raise ValueError(f"不支持的登录方式: {method}，仅支持 {'/'.join(LOGIN_METHODS)}")
    if display not in DISPLAY_MODES:
        raise ValueError(f"不支持的展示方式: {display}，仅支持 {'/'.join(DISPLAY_MODES)}")
    if open_browser and display != "browser":
        raise ValueError("`--open-browser` 仅能与 `--display browser` 搭配使用")
    if timeout <= 0:
        raise ValueError("`--timeout` 必须为正数")

    store = ctx.get_store()
    actual_cookie_file = cookie_file or ctx.cookie_file
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform, client)
        if method == "cookie":
            if not actual_cookie_file:
                raise ValueError("Cookie 登录模式必须指定 `--cookie-file` 参数")
            cookies = _parse_cookie_payload(_read_json_file_or_stdin(actual_cookie_file))
            user = await plat.login(cookies=cookies)
        else:
            user = await plat.login(display=display, open_browser=open_browser, timeout=timeout)

        if plat.current_cookies:
            store.save(platform, user, plat.current_cookies)

        data = LoginResult(
            platform=platform,
            user_id=str(user.id),
            name=user.name,
            school=user.school,
            persisted=ctx.credential_mode == "system",
        )
        next_actions = [
            NextAction(
                reason="查看该账号课程列表",
                argv=["wkhelper", "course", "list", "--platform", platform, "--account", str(user.id)],
            )
        ]
        return CommandResult(data=data, next_actions=next_actions)


@auth_app.command(name="remove")
async def auth_remove_cmd(
    platform: Annotated[str, Parameter(name=["--platform", "-p"], help="平台代码")],
    account: Annotated[str, Parameter(name=["--account", "-a"], help="账号 ID")],
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认删除")] = False,
) -> CommandResult[RemoveResult]:
    """删除指定账号凭据。"""
    _validate_platform_code(platform)
    if not confirm:
        raise ConfirmationRequiredError(
            message="删除账号是本地破坏性操作，必须显式附加 `--confirm` 参数",
            next_actions=[
                NextAction(
                    reason="确认删除",
                    argv=["wkhelper", "auth", "remove", "--platform", platform, "--account", account, "--confirm"],
                )
            ],
        )
    store = ctx.get_store()
    deleted = store.delete(platform, account)
    return CommandResult(data=RemoveResult(platform=platform, account=account, deleted=deleted))


auth_storage_app = auth_app.command(
    App(name="storage", help="系统存储后端状态", help_formatter="plain", result_action=unified_result_action)
)


@auth_storage_app.command(name="status")
async def auth_storage_status_cmd() -> CommandResult[StorageStatus]:
    """查看系统凭据存储后端状态。"""
    return CommandResult(data=ctx.get_store().get_storage_status())


# course 命令群


@course_app.command(name="list")
async def course_list_cmd(
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
    limit: Annotated[int, Parameter(name=["--limit"], help="每页条数")] = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
) -> CommandResult[CourseListData]:
    """查询当前账号的课程列表。"""
    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        courses = dedupe_courses(await plat.get_courses())
        items = [
            CourseItem(
                id=course_reference(course),
                name=course.name,
                platform=course.platform_id,
                classroom_id=msgspec.to_builtins(course.metadata.get("classroom_id")),
            )
            for course in courses
        ]
        page_items, meta = _paginate(items, limit, cursor)
        next_actions: list[NextAction] = []
        if page_items:
            next_actions.append(NextAction(reason="查看首门课程视频", argv=["wkhelper", "video", "list", "--course", page_items[0].id]))
        return CommandResult(data=CourseListData(courses=page_items), meta=meta.to_dict(), next_actions=next_actions)


@course_app.command(name="inspect")
async def course_inspect_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[CourseDetail]:
    """查看指定课程的详细信息。"""
    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        matched = await _load_course(plat, course)
        return CommandResult(
            data=CourseDetail(
                id=course_reference(matched),
                name=matched.name,
                platform=matched.platform_id,
                metadata=_metadata_to_json(matched.metadata),
            )
        )


# video 命令群


@video_app.command(name="list")
async def video_list_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    status: Annotated[str, Parameter(name=["--status"], help="状态过滤 (pending, completed, all)")] = "all",
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
    limit: Annotated[int, Parameter(name=["--limit"], help="每页条数")] = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
) -> CommandResult[VideoListData]:
    """查询课程视频列表。"""
    _validate_status_filter(status)
    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        matched = await _load_course(plat, course)
        videos = await plat.get_videos(matched)
        raw_completed = matched.metadata.get("completed_video_ids")
        completed_ids: set[int] = raw_completed if isinstance(raw_completed, set) else set()

        items: list[VideoItem] = []
        for video_id, label in videos.items():
            is_completed = video_id in completed_ids or "[已完成]" in label
            if status == "pending" and is_completed:
                continue
            if status == "completed" and not is_completed:
                continue
            items.append(VideoItem(id=str(video_id), name=label.replace(" [已完成]", ""), completed=is_completed))

        page_items, meta = _paginate(items, limit, cursor)
        return CommandResult(data=VideoListData(videos=page_items), meta=meta.to_dict())


@video_app.command(name="inspect")
async def video_inspect_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    video: Annotated[str, Parameter(name=["--video", "-v"], help="视频 ID")],
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[VideoProgress]:
    """查询单视频进度与执行约束。"""
    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        matched = await _load_course(plat, course)
        return CommandResult(data=await plat.get_video_progress(video, matched))


@video_app.command(name="learn")
async def video_learn_cmd(
    course: Annotated[str | None, Parameter(name=["--course", "-c"], help="课程 ID")] = None,
    video: Annotated[list[str] | None, Parameter(name=["--video", "-v"], help="视频 ID（可多次指定）")] = None,
    plan_file: Annotated[str | None, Parameter(name=["--plan-file"], help="批量计划 JSON 文件")] = None,
    concurrency: Annotated[int | None, Parameter(name=["--concurrency"], help="并发任务数")] = None,
    learning_rate: Annotated[float | None, Parameter(name=["--learning-rate"], help="推进速率乘数")] = None,
    heartbeat_interval: Annotated[float | None, Parameter(name=["--heartbeat-interval"], help="心跳间隔（秒）")] = None,
    progress_interval: Annotated[float | None, Parameter(name=["--progress-interval"], help="进度复查间隔（秒）")] = None,
    completion_threshold: Annotated[float | None, Parameter(name=["--completion-threshold"], help="完成进度阈值 (0, 1]")] = None,
    video_timeout: Annotated[float | None, Parameter(name=["--video-timeout"], help="单视频总超时（秒）")] = None,
    total_timeout: Annotated[float | None, Parameter(name=["--total-timeout"], help="批任务总超时（秒）")] = None,
    stall_timeout: Annotated[float | None, Parameter(name=["--stall-timeout"], help="无进展停滞超时（秒）")] = None,
    max_heartbeats: Annotated[int | None, Parameter(name=["--max-heartbeats"], help="单视频最大心跳数")] = None,
    on_error: Annotated[str | None, Parameter(name=["--on-error"], help="出错策略 (stop 或 continue)")] = None,
    checkpoint_file: Annotated[str | None, Parameter(name=["--checkpoint-file"], help="检查点状态文件")] = None,
    resume_from: Annotated[str | None, Parameter(name=["--resume-from"], help="从检查点文件恢复执行")] = None,
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="仅预览计划，不执行写操作")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认执行写操作")] = False,
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[VideoLearnPreview] | CommandResult[VideoPlanSummary]:
    """执行视频学习流程。"""
    _validate_platform_code(platform)

    plan: VideoPlan | None = None
    confirm_argv: list[str] = ["wkhelper", "video", "learn"]

    if plan_file is not None:
        if course is not None or video is not None:
            raise ValueError("`--plan-file` 与 `--course` / `--video` 互斥")
        plan = _load_plan(plan_file)
        if plan.platform is not None and platform is not None and plan.platform != platform:
            raise ValueError(f"计划平台 '{plan.platform}' 与命令行 `--platform {platform}` 冲突")
        if plan.account is not None and account is not None and plan.account != account:
            raise ValueError(f"计划账号 '{plan.account}' 与命令行 `--account {account}` 冲突")
        platform = platform if platform is not None else plan.platform
        account = account if account is not None else plan.account
        targets = plan.targets
        base_execution = plan.execution
        plan_defaults = plan.video_defaults
        confirm_argv += ["--plan-file", plan_file]
    else:
        if course is None:
            raise ValueError("必须通过 `--course` 与 `--video`，或通过 `--plan-file` 指定学习目标")
        video_ids = video or []
        if not video_ids:
            raise ValueError("必须通过 `--course` 与 `--video`，或通过 `--plan-file` 指定学习目标")
        targets = [VideoTarget(course_id=str(course), video_id=str(video_id)) for video_id in video_ids]
        base_execution = ExecutionSettings()
        plan_defaults = VideoParameters()
        confirm_argv += ["--course", str(course)]
        for video_id in video_ids:
            confirm_argv += ["--video", str(video_id)]

    cli_parameters = VideoParameters(
        learning_rate=learning_rate,
        heartbeat_interval=heartbeat_interval,
        progress_interval=progress_interval,
        completion_threshold=completion_threshold,
        video_timeout=video_timeout,
        stall_timeout=stall_timeout,
        max_heartbeats=max_heartbeats,
    )
    cli_parameters.validate("命令行参数")
    effective_execution = ExecutionOverrides(concurrency=concurrency, on_error=on_error, total_timeout=total_timeout).overlay(
        base_execution
    )

    if dry_run:
        return CommandResult[VideoLearnPreview](
            data=VideoLearnPreview(
                dry_run=True,
                targets_count=len(targets),
                targets=targets,
                requested_config=base_execution,
                effective_config=effective_execution,
                note="预览模式不产生网络心跳写入，确认执行请传入 `--confirm`",
            )
        )

    if not confirm:
        raise ConfirmationRequiredError(
            message="视频学习操作涉及向平台提交心跳，必须附加 `--confirm` 参数",
            next_actions=[NextAction(reason="确认执行视频学习", argv=[*confirm_argv, "--confirm"])],
        )

    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        # 同一课程可能挂在多个课堂下，按课堂级引用建索引，避免空课堂顶掉有内容的课堂
        courses_map = {course_reference(course_item): course_item for course_item in dedupe_courses(await plat.get_courses())}
        runner = Runner(plat)
        summary = await runner.execute_video_plan(
            courses_map=courses_map,
            targets=targets,
            concurrency=effective_execution.concurrency,
            on_error=effective_execution.on_error,
            default_options=VideoWatchOptions(total_timeout=effective_execution.total_timeout),
            plan_parameters=plan_defaults,
            cli_parameters=cli_parameters,
            checkpoint_file=checkpoint_file,
            resume_from=resume_from,
        )
        if summary.failed > 0:
            if summary.completed == 0:
                raise APIError("全部视频任务执行失败")
            raise PartialFailureError("部分视频任务执行失败", data=summary)
        return CommandResult[VideoPlanSummary](data=summary)


# homework 命令群


@homework_app.command(name="list")
async def homework_list_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    status: Annotated[str, Parameter(name=["--status"], help="状态过滤")] = "all",
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
    limit: Annotated[int, Parameter(name=["--limit"], help="每页条数")] = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
) -> CommandResult[HomeworkListData]:
    """查询课程作业列表。"""
    _validate_status_filter(status)
    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        matched = await _load_course(plat, course)
        homeworks = await plat.get_homeworks(matched)

        items: list[HomeworkItem] = []
        for homework in homeworks:
            if status == "pending" and homework.completed:
                continue
            if status == "completed" and not homework.completed:
                continue
            items.append(
                HomeworkItem(
                    id=str(homework.id),
                    name=homework.name,
                    deadline=homework.deadline,
                    completed=homework.completed,
                )
            )

        page_items, meta = _paginate(items, limit, cursor)
        return CommandResult(data=HomeworkListData(homeworks=page_items), meta=meta.to_dict())


@homework_app.command(name="inspect")
async def homework_inspect_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    homework: Annotated[str, Parameter(name=["--homework", "-w"], help="作业 ID")],
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[HomeworkOutline]:
    """查看作业元数据及题目大纲。"""
    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        matched = await _load_course(plat, course)
        questions = await plat.get_leaf_questions(homework, matched)

        outlines: list[QuestionOutline] = []
        for index, question in enumerate(questions, 1):
            content = question.get("content", {})
            outlines.append(
                QuestionOutline(
                    index=index,
                    id=str(question.get("problem_id") or question.get("id")),
                    type=question.get("type"),
                    library_id=content.get("LibraryID"),
                )
            )

        return CommandResult(
            data=HomeworkOutline(
                course_id=str(matched.id),
                homework_id=str(homework),
                total_questions=len(questions),
                questions_summary=outlines,
            )
        )


@homework_app.command(name="submit")
async def homework_submit_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    homework: Annotated[str, Parameter(name=["--homework", "-w"], help="作业 ID")],
    answers_file: Annotated[str | None, Parameter(name=["--answers-file"], help="答案 JSON 文件")] = None,
    answers: Annotated[str | None, Parameter(name=["--answers"], help="直接传入 JSON 格式答案字典")] = None,
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="仅校验，不提交")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认提交答案")] = False,
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[HomeworkSubmitPreview] | CommandResult[HomeworkSubmitResult]:
    """提交作业答案集合。"""
    if answers is not None and answers_file is not None:
        raise ValueError("`--answers` 与 `--answers-file` 只能提供其一")
    if answers is not None:
        raw_answers = parse_json_text(answers)
    elif answers_file is not None:
        raw_answers = _read_json_file_or_stdin(answers_file)
    else:
        raise ValueError("提交作业必须通过 `--answers` 或 `--answers-file` 至少提供一种答案输入")

    answers_map = _parse_answers_payload(raw_answers)
    if not answers_map:
        raise ValueError("答案内容为空或格式无效，未提取到任何题目的有效答案")

    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        matched = await _load_course(plat, course)

        # 校验题目归属与选项合法性
        questions = await plat.get_leaf_questions(homework, matched)
        questions_by_id: dict[str, dict[str, Any]] = {
            str(question.get("problem_id") or question.get("id")): question for question in questions
        }
        for question_id, answer_choices in answers_map.items():
            question = questions_by_id.get(question_id)
            if question is None:
                raise ValueError(f"题目 ID '{question_id}' 不属于作业 '{homework}'，可用题目 ID: {sorted(questions_by_id)}")
            options = question.get("content", {}).get("Options") or []
            valid_keys = {str(option.get("key")) for option in options if option.get("key")}
            if valid_keys:
                for option_value in answer_choices:
                    if option_value not in valid_keys:
                        raise ValueError(f"题目 '{question_id}' 的答案选项 '{option_value}' 无效，合法选项为: {sorted(valid_keys)}")

        if dry_run:
            return CommandResult[HomeworkSubmitPreview](
                data=HomeworkSubmitPreview(
                    dry_run=True,
                    course_id=str(matched.id),
                    homework_id=str(homework),
                    answers_count=len(answers_map),
                    validated=True,
                )
            )

        if not confirm:
            raise ConfirmationRequiredError("作业提交必须显式附加 `--confirm` 参数")

        runner = Runner(plat)
        result = await runner.execute_homework_submit(
            matched,
            Homework(id=str(homework), name=str(homework)),
            answers=answers_map,
        )
        if result.submitted_count < result.total:
            raise PartialFailureError("部分题目提交失败", data=result)
        return CommandResult[HomeworkSubmitResult](data=result)


@question_app.command(name="list")
async def question_list_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    homework: Annotated[str, Parameter(name=["--homework", "-w"], help="作业 ID")],
    limit: Annotated[int, Parameter(name=["--limit"], help="每页条数")] = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[QuestionListData]:
    """分页获取作业题目列表与作答状态。"""
    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        matched = await _load_course(plat, course)
        questions = await plat.get_leaf_questions(homework, matched)

        items: list[QuestionSummary] = []
        for index, question in enumerate(questions, 1):
            content = question.get("content", {})
            body = html_to_md(content.get("Body") or content.get("body") or "")
            options = content.get("Options") or []
            user_info = question.get("user", {})
            user_answer = user_info.get("answer")
            attempt_count = json_int(user_info.get("my_count"))
            items.append(
                QuestionSummary(
                    index=index,
                    id=str(question.get("problem_id") or question.get("id")),
                    type=question.get("type"),
                    prompt=body[:200] + ("..." if len(body) > 200 else ""),
                    content_truncated=len(body) > 200,
                    options=[OptionItem(key=option.get("key"), text=html_to_md(option.get("value", ""))) for option in options],
                    submitted=bool(user_answer or attempt_count > 0),
                    is_correct=user_info.get("is_right"),
                )
            )

        page_items, meta = _paginate(items, limit, cursor)
        return CommandResult(data=QuestionListData(questions=page_items), meta=meta.to_dict())


@question_app.command(name="get")
async def question_get_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    homework: Annotated[str, Parameter(name=["--homework", "-w"], help="作业 ID")],
    question: Annotated[str, Parameter(name=["--question", "-q"], help="题目 ID")],
    output: Annotated[str | None, Parameter(name=["--output", "-o"], help="输出文件路径")] = None,
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[QuestionDetail] | CommandResult[OutputFileResult]:
    """获取单题完整详情与作答结果。"""
    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        matched = await _load_course(plat, course)
        questions = await plat.get_leaf_questions(homework, matched)
        target_question = next((item for item in questions if str(item.get("problem_id") or item.get("id")) == str(question)), None)
        if target_question is None:
            raise KeyError(f"未找到题目 ID: {question}")

        content = target_question.get("content", {})
        body = html_to_md(content.get("Body") or content.get("body") or "")
        options = content.get("Options") or []
        user_info = target_question.get("user", {})
        attempt_count = json_int(user_info.get("my_count"))
        detail = QuestionDetail(
            id=str(question),
            type=target_question.get("type"),
            prompt=body,
            options=[OptionItem(key=option.get("key"), text=html_to_md(option.get("value", ""))) for option in options],
            submitted=bool(user_info.get("answer") or attempt_count > 0),
            user_answer=user_info.get("answer"),
            is_correct=user_info.get("is_right"),
            score=user_info.get("score"),
            attempt_count=attempt_count,
            library_id=content.get("LibraryID") or content.get("library_id"),
            version=content.get("Version"),
        )
        if output is not None:
            Path(output).write_bytes(msgspec.json.encode(detail))
            return CommandResult[OutputFileResult](data=OutputFileResult(output_file=output, written=True))
        return CommandResult[QuestionDetail](data=detail)


@question_app.command(name="submit")
async def question_submit_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    homework: Annotated[str, Parameter(name=["--homework", "-w"], help="作业 ID")],
    question: Annotated[str, Parameter(name=["--question", "-q"], help="题目 ID")],
    answer: Annotated[str, Parameter(name=["--answer", "-a"], help="答案选项 (例如 'A' 或 'A,B')")],
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="校验参数，不提交")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认提交答案")] = False,
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account"], help="账号 ID")] = None,
) -> CommandResult[QuestionSubmitPreview] | CommandResult[QuestionSubmitResult]:
    """提交单道题目答案并返回判题结果。"""
    answer_list = [item.strip() for item in answer.split(",") if item.strip()]
    if not answer_list:
        raise ValueError(f"题目答案参数无效: {answer}")

    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        matched = await _load_course(plat, course)

        # 校验题目存在性与选项合法性
        questions = await plat.get_leaf_questions(homework, matched)
        target_question = next((item for item in questions if str(item.get("problem_id") or item.get("id")) == str(question)), None)
        if target_question is None:
            raise KeyError(f"题目 ID '{question}' 不属于作业 '{homework}'")
        options = target_question.get("content", {}).get("Options") or []
        valid_keys = {str(option.get("key")) for option in options if option.get("key")}
        if valid_keys:
            for option_value in answer_list:
                if option_value not in valid_keys:
                    raise ValueError(f"题目 '{question}' 的答案选项 '{option_value}' 无效，合法选项为: {sorted(valid_keys)}")

        if dry_run:
            return CommandResult[QuestionSubmitPreview](
                data=QuestionSubmitPreview(
                    dry_run=True,
                    course_id=str(matched.id),
                    homework_id=str(homework),
                    question_id=str(question),
                    answer=answer_list,
                )
            )

        if not confirm:
            raise ConfirmationRequiredError("题目提交必须显式附加 `--confirm` 参数")

        result = await plat.submit_question(
            Homework(id=str(homework), name=str(homework)),
            matched,
            question,
            answer_list,
        )
        if not result.success:
            raise APIError("题目提交失败")
        return CommandResult[QuestionSubmitResult](data=result)


@questions_app.command(name="export")
async def questions_export_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    output: Annotated[str, Parameter(name=["--output", "-o"], help="输出 Markdown 文件路径")],
    platform: Annotated[str | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExportResult]:
    """导出课程题目为 Markdown 文件。"""
    store = ctx.get_store()
    platform_code, _account_id, cookies = _resolve_account_and_platform(store, platform, account)
    async with niquests.AsyncSession(timeout=15) as client:
        plat = _create_platform_instance(platform_code, client)
        await plat.login(cookies=cookies)
        matched = await _load_course(plat, course)
        return CommandResult(data=await export_questions_to_markdown(plat, matched, output_path=output))
