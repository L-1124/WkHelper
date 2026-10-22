"""CLI 命令规范定义与 Schema 自省。

命令树信息全部通过 Cyclopts 公开 API（`iter`/`__getitem__`/`assemble_argument_collection`）
获取，不依赖私有属性；摘要与描述以 `App.help` 和 docstring 为唯一真相，
`COMMAND_CONTRACTS` 只补充无法从代码推导的契约字段（副作用、确认要求、示例）。
"""

import inspect
from functools import cache
from typing import Any

from cyclopts import App
from pydantic import Field, JsonValue, TypeAdapter

from wkhelper.domain.model import Model


class ArgumentSpec(Model):
    name: str
    flags: list[str]
    negative_flags: list[str] = Field(default_factory=list)
    type: str = ""
    items: str | None = None
    required: bool = False
    default: JsonValue = None
    help: str = ""
    choices: list[str] | None = None
    positional: bool = False
    keyword_only: bool = False
    allow_repeating: bool = False


class CommandSpec(Model):
    path: str
    summary: str
    description: str
    arguments: list[ArgumentSpec] = Field(default_factory=list)
    session_options: list[ArgumentSpec] = Field(default_factory=list)
    side_effects: str = "none"
    requires_confirm: bool = False
    examples: list[str] = Field(default_factory=list)


COMMAND_CONTRACTS: dict[str, dict[str, Any]] = {
    "status": {
        "description": "读取本地配置、默认数据目录及已存储账号摘要（不进行网络认证请求）。",
        "examples": ["wkhelper status"],
    },
    "schema": {
        "description": "按层级查看命令规范、参数定义、副作用与示例。",
        "examples": [
            "wkhelper schema",
            "wkhelper schema 'video learn'",
            "wkhelper schema 'video learn' --full",
        ],
    },
    "auth list": {
        "description": "查询本地已保存的账号列表，绝不输出敏感 Cookie 或密码。",
        "examples": ["wkhelper auth list", "wkhelper auth list --platform ykt"],
    },
    "auth status": {
        "description": "使用指定账号向平台发起轻量只读请求，检查凭据是否依然有效。",
        "examples": [
            "wkhelper auth status --platform ykt",
            "wkhelper auth status --platform ykt --account 123456",
        ],
    },
    "auth login": {
        "description": "通过扫码或导入 Cookie 登录并保存至系统安全凭据管理器。",
        "side_effects": "local_write",
        "examples": [
            "wkhelper auth login --platform ykt --method qrcode",
            "wkhelper auth login --platform ykt --method qrcode --display browser --open-browser",
            "wkhelper auth login --platform ykt --method cookie --cookie-file cookies.json",
        ],
    },
    "auth remove": {
        "description": "从系统凭据管理器和本地元数据中删除账号凭据。",
        "side_effects": "local_delete",
        "requires_confirm": True,
        "examples": ["wkhelper auth remove --platform ykt --account 123456 --confirm"],
    },
    "auth storage status": {
        "description": "检查当前操作系统 keyring 后端类型与可用性。",
        "examples": ["wkhelper auth storage status"],
    },
    "course list": {
        "description": "有界分页列出当前账号下的所有在学课程。",
        "examples": ["wkhelper course list --platform ykt", "wkhelper course list --limit 5"],
    },
    "course inspect": {
        "description": "查询指定课程的元数据与大纲概要。",
        "examples": ["wkhelper course inspect --course 1001 --platform ykt"],
    },
    "video list": {
        "description": "有界分页列出指定课程中的视频叶子节点、完成状态与时长。",
        "examples": ["wkhelper video list --course 1001 --status pending"],
    },
    "video inspect": {
        "description": "查询服务端当前记录的视频播放进度、观看长度与可用参数边界。",
        "examples": ["wkhelper video inspect --course 1001 --video 2001"],
    },
    "video learn": {
        "description": "向平台发送心跳推进学习进度，支持单/多视频或计划文件，全面开放控制参数。",
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper video learn --course 1001 --video 2001 --dry-run",
            "wkhelper video learn --course 1001 --video 2001 --confirm",
            "wkhelper video learn --plan-file plan.json --confirm",
        ],
    },
    "video snapshot": {
        "description": (
            "基于原生浏览器对指定视频进行纯净抽帧，支持特定时间戳、步长范围或均匀采样"
            "（默认开启 Warm Session 进程池复用，支持 --no-cache 禁用；--clean-cache 清理后台热会话）。"
        ),
        "side_effects": "none",
        "examples": [
            "wkhelper video snapshot --course 1001 --video 2001 --timestamps 30,60,90",
            "wkhelper video snapshot --course 1001 --video 2001 --count 5",
            "wkhelper video snapshot --course 1001 --video 2001 --start 300 --end 600 --step 30",
            "wkhelper video snapshot --course 1001 --video 2001 --timestamps 45 --no-cache",
            "wkhelper video snapshot --clean-cache",
        ],
    },
    "homework list": {
        "description": "有界分页列出指定课程的作业、截止时间与完成状态。",
        "examples": ["wkhelper homework list --course 1001 --status pending"],
    },
    "homework inspect": {
        "description": "查询作业详情、题目总数及题目类型分布，不内嵌完整题干全文。",
        "examples": ["wkhelper homework inspect --course 1001 --homework 3001"],
    },
    "homework submit": {
        "description": (
            "批量提交作业答案；支持 --delay、--delay-jitter 与 --random-order。默认单题 8s 基础间隔与 3s 随机浮动。"
            "建议依据题干长短与难度（客观题 5~10s、多选 15~35s、主观题 45~90s）动态调整延迟，"
            "并开启 --random-order 打乱题目顺序，模拟真人思考作答以规避风控。"
        ),
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            'wkhelper homework submit -c 1001 -w 3001 --answers \'{"4001": ["A"]}\' --delay 8 --delay-jitter 3 --random-order --confirm',
            "wkhelper homework submit --course 1001 --homework 3001 --answers-file ans.json --confirm",
        ],
    },
    "question list": {
        "description": "按页读取题目摘要、选项与修订版本号。",
        "examples": ["wkhelper question list --course 1001 --homework 3001 --limit 5"],
    },
    "question get": {
        "description": "获取单道题目的完整反混淆题干、公式、选项与附件引用。",
        "examples": ["wkhelper question get --course 1001 --homework 3001 --question 4001"],
    },
    "question submit": {
        "description": (
            "直接向作业提交单道题目答案并返回判题结果；支持 --delay 与 --delay-jitter。"
            "建议依据题目难易程度（客观题 5~15s，计算题 20~40s）动态设置等待思考时长，模拟真实读题思考。"
        ),
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper question submit --course 1001 --homework 3001 --question 4001 --answer A --dry-run",
            "wkhelper question submit --course 1001 --homework 3001 --question 4001 --answer A --delay 8 --confirm",
            "wkhelper question submit --course 1001 --homework 3001 --question 4001 --answer A,B --confirm",
        ],
    },
    "article list": {
        "description": "有界分页列出指定课程的资料阅读与图文节点及完成状态。",
        "examples": ["wkhelper article list --course 1001 --status pending"],
    },
    "article read": {
        "description": "向平台发送完成信号，将指定的资料/图文节点标记为已读。",
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper article read --course 1001 --article 2001 --dry-run",
            "wkhelper article read --course 1001 --article 2001 --confirm",
        ],
    },
    "discussion list": {
        "description": "有界分页列出指定课程的讨论互动节点及是否发言状态。",
        "examples": ["wkhelper discussion list --course 1001 --status pending"],
    },
    "discussion inspect": {
        "description": "查询讨论题节点题目大纲、教师题干要求与当前参与人数统计。",
        "examples": ["wkhelper discussion inspect --course 1001 --discussion 3001"],
    },
    "discussion reply": {
        "description": "向指定讨论题提交发言内容，获取平时分考核进度。",
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper discussion reply --course 1001 --discussion 3001 --content '心得体会...' --dry-run",
            "wkhelper discussion reply --course 1001 --discussion 3001 --content '心得体会...' --confirm",
        ],
    },
    "notice list": {
        "description": "有界分页列出指定课程的公告/通知节点及已读状态（`leaf_type=10`）。",
        "examples": ["wkhelper notice list --course 1001 --status pending"],
    },
    "notice inspect": {
        "description": "读取公告正文（Markdown）、发布者与发布时间。",
        "examples": ["wkhelper notice inspect --course 1001 --notice 4001"],
    },
    "exam list": {
        "description": "有界分页列出指定课程的期中/期末考试、开考时间与截止时间。",
        "examples": ["wkhelper exam list --course 1001"],
    },
    "exam inspect": {
        "description": "查看考试封面、考试须知、满分、限时时长与考场状态。",
        "examples": ["wkhelper exam inspect --course 1001 --exam 2001"],
    },
    "exam status": {
        "description": "汇总考场 cover/refresh_time/cache_results/problem_results 状态（只读诊断）。",
        "examples": ["wkhelper exam status --course 1001 --exam 2001"],
    },
    "exam result": {
        "description": "读取考场成绩与作答结果概览（只读）。",
        "examples": ["wkhelper exam result --course 1001 --exam 2001"],
    },
    "exam close": {
        "description": "关闭 exam start 拉起的考场浏览器会话并清理会话文件。",
        "side_effects": "local_write",
        "examples": ["wkhelper exam close"],
    },
    "exam answer": {
        "description": (
            "在已打开的前台考场浏览器页面中模拟点击选项或输入文本完成单题作答，由考场前端页面自动暂存。"
            "必须先用 `exam start --confirm` 打开前台考场浏览器。"
            "`saved` 要求页面请求内容一致且返回有效业务成功码；未捕获请求或响应证据未知时回读一次暂存作答，明确拒绝不回读。"
            "强监考考试（在线监考/人脸核验/网页截屏/线下考场）一律拒绝执行，已交卷或已缺考的考试同样拒绝。"
            "客观题用逗号分隔多个选项键（判断题自动映射正确/错误），填空题按空逗号分隔，主观题整段填入。"
        ),
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper exam answer --course 1001 --exam 2001 --question 3001 --answer A --dry-run",
            "wkhelper exam answer --course 1001 --exam 2001 --question 3001 --answer A --delay 10 --delay-jitter 3 --confirm",
            "wkhelper exam answer --course 1001 --exam 2001 --question 3002 --answer '极限存在且等于该点函数值' --confirm",
        ],
    },
    "exam export": {
        "description": "把考场试卷导出为 Markdown，可下载题干与选项配图，拒绝覆盖已有文件。",
        "side_effects": "local_file_write",
        "examples": ["wkhelper exam export --course 1001 --exam 2001 --output ./exam.md"],
    },
    "exam paper": {
        "description": "读取考场子系统下发的整卷题目（只读，不提交答案）。",
        "examples": ["wkhelper exam paper --course 1001 --exam 2001"],
    },
    "exam start": {
        "description": (
            "显式确认后向平台申请考场会话令牌并打开前台考场浏览器，保持考场页面以维持监考与心跳采集。"
            "传入 `--begin` 会在前台浏览器中点击开始答题按钮，经网页发出开始答题请求并验证进入答题页后生效；"
            "`--dry-run` 仅预览；强监考考试拒绝进入。"
        ),
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper exam start --course 1001 --exam 2001 --dry-run",
            "wkhelper exam start --course 1001 --exam 2001 --confirm",
            "wkhelper exam start --course 1001 --exam 2001 --begin --confirm",
        ],
    },
}


def _resolve_type_and_items(hint: Any) -> tuple[str, str | None]:
    """利用 Pydantic TypeAdapter 提取标准 JSON Schema 类型与容器元素类型。"""
    try:
        schema = TypeAdapter(hint).json_schema()
    except Exception:
        return "string", None

    schemas = [s for s in schema.get("anyOf", [schema]) if s.get("type") != "null"]
    type_map = {"integer": "int", "number": "float", "boolean": "bool"}
    type_names = list(dict.fromkeys(type_map.get(s.get("type", "string"), s.get("type", "string")) for s in schemas))
    items_list: list[str] = []
    for s in schemas:
        if s.get("type") == "array" and "items" in s:
            raw_item_type = s["items"].get("type", "string")
            items_list.append(type_map.get(raw_item_type, raw_item_type))

    return " | ".join(type_names) if type_names else "string", " | ".join(dict.fromkeys(items_list)) or None


def _build_argument_spec(arg: Any) -> ArgumentSpec:
    """基于 Cyclopts Argument 的真实解析元数据构建参数契约规范。"""
    name = arg.field_info.names[0]
    negatives = list(arg.negatives)
    flags = [name for name in arg.names if name not in negatives]

    required = bool(arg.required)
    default = None if required or arg.field_info.default is inspect.Parameter.empty else arg.field_info.default
    help_str = arg.parameter.help or arg.field_info.help or ""
    type_str, items_str = _resolve_type_and_items(arg.hint)
    raw_choices = arg.get_choices()
    choices = [str(choice) for choice in raw_choices] if raw_choices else None
    positional = arg.index is not None
    keyword_only = arg.index is None
    allow_repeating = bool(arg.parameter.allow_repeating) if arg.parameter.allow_repeating is not None else (type_str == "array")

    return ArgumentSpec(
        name=name,
        flags=flags,
        negative_flags=negatives,
        type=type_str,
        items=items_str,
        required=required,
        default=default,
        help=help_str,
        choices=choices,
        positional=positional,
        keyword_only=keyword_only,
        allow_repeating=allow_repeating,
    )


def _inspect_command_arguments(cmd_app: App) -> list[ArgumentSpec]:
    """从 Cyclopts 命令应用中内省解析参数列表。"""
    args_col = cmd_app.assemble_argument_collection()
    return [_build_argument_spec(arg) for arg in args_col if arg.show]


@cache
def get_session_options_specs() -> list[ArgumentSpec]:
    """从根应用的 meta 装配中提取全局会话参数规范（扁平化展开并排除内部 tokens）。"""
    from wkhelper.cli.app import app

    meta_col = app.meta.assemble_argument_collection()
    return [_build_argument_spec(arg) for arg in meta_col if arg.show]


def _hidden_command_names(node: App) -> set[str]:
    """Collect the auto-registered help/version command names of an app."""
    hidden: set[str] = set()
    for flags in (node.help_flags, node.version_flags):
        if isinstance(flags, str):
            hidden.add(flags)
        elif flags:
            hidden.update(str(flag) for flag in flags)
    return hidden


def _business_command_names(node: App) -> list[str]:
    """List public business command names, excluding help/version built-ins."""
    hidden = _hidden_command_names(node)
    return [name for name in node if name not in hidden]


def _collect_leaf_commands(node: App, prefix: str = "") -> dict[str, App]:
    """递归遍历 Cyclopts 命令树（仅使用公开 API），收集所有叶子业务命令。"""
    leaf_map: dict[str, App] = {}
    for name in _business_command_names(node):
        sub = node[name]
        full_path = f"{prefix} {name}".strip()
        if _business_command_names(sub):
            leaf_map.update(_collect_leaf_commands(sub, full_path))
        else:
            leaf_map[full_path] = sub
    return leaf_map


def _build_command_spec(path: str, sub_app: App) -> CommandSpec:
    """构建单个命令的契约规范：摘要/描述取自 App.help 与 docstring，其余取自契约补充表。"""
    contract = COMMAND_CONTRACTS.get(path, {})
    doc = getattr(sub_app.default_command, "__doc__", "") or ""
    doc_summary = doc.strip().split("\n")[0].strip(" .。") if doc else ""
    summary = (sub_app.help or doc_summary or path).strip().rstrip("。.")
    description = contract.get("description") or doc_summary or summary
    side_effects = contract.get("side_effects", "none")
    requires_confirm = contract.get("requires_confirm", False)
    examples = contract.get("examples", [])
    arguments = _inspect_command_arguments(sub_app)
    session_options = get_session_options_specs()

    return CommandSpec(
        path=path,
        summary=summary,
        description=description,
        arguments=arguments,
        session_options=session_options,
        side_effects=side_effects,
        requires_confirm=requires_confirm,
        examples=examples,
    )


@cache
def command_registry() -> dict[str, CommandSpec]:
    """构建命令规范注册表。

    首次访问时才导入根应用并从 Cyclopts 命令树生成，避免 `spec` 与 `app` 的循环导入。
    """
    from wkhelper.cli.app import app

    return {path: _build_command_spec(path, node) for path, node in _collect_leaf_commands(app).items()}


def get_command_spec(path: str, full: bool = False) -> CommandSpec:
    """获取指定路径的命令契约规范。

    Args:
        path: 命令路径，例如 'video learn'
        full: 为 True 时返回包含完整参数类型、帮助和选项列表的规范；
              为 False 时返回精简参数规范。
    """
    path = path.strip()
    registry = command_registry()
    if path not in registry:
        raise KeyError(f"未知的命令路径: {path}")
    spec = registry[path]
    if full:
        return spec
    compact_args = [
        ArgumentSpec(
            name=arg.name,
            flags=arg.flags,
            negative_flags=arg.negative_flags,
            type="",
            items=None,
            required=arg.required,
            default=arg.default,
            help="",
            choices=None,
            positional=arg.positional,
            keyword_only=arg.keyword_only,
            allow_repeating=arg.allow_repeating,
        )
        for arg in spec.arguments
    ]
    compact_session_options = [
        ArgumentSpec(
            name=opt.name,
            flags=opt.flags,
            negative_flags=opt.negative_flags,
            type="",
            items=None,
            required=opt.required,
            default=opt.default,
            help="",
            choices=None,
            positional=opt.positional,
            keyword_only=opt.keyword_only,
            allow_repeating=opt.allow_repeating,
        )
        for opt in spec.session_options
    ]
    return CommandSpec(
        path=spec.path,
        summary=spec.summary,
        description=spec.description,
        arguments=compact_args,
        session_options=compact_session_options,
        side_effects=spec.side_effects,
        requires_confirm=spec.requires_confirm,
        examples=spec.examples,
    )


class NextActionFieldSchema(Model):
    type: str
    description: str
    items: str | None = Field(default=None, exclude_if=lambda v: not v)


class NextActionSchema(Model):
    """NextAction 专属的契约 Schema。"""

    type: str = "object"
    description: str = "供 AI Agent 下一步自引导决策的结构化命令动作"
    properties: dict[str, NextActionFieldSchema] = Field(
        default_factory=lambda: {
            "reason": NextActionFieldSchema(type="string", description="执行该动作的意图与业务理由"),
            "argv": NextActionFieldSchema(type="array", items="string", description="完整的命令行执行参数列表"),
            "description": NextActionFieldSchema(type="string", description="可选的详细动作解释"),
        }
    )
    required: list[str] = Field(default_factory=lambda: ["reason", "argv"])


NEXT_ACTION_SCHEMA = NextActionSchema()


class ParameterSchema(Model):
    name: str
    flags: list[str]
    negative_flags: list[str] = Field(default_factory=list, exclude_if=lambda v: not v)
    required: bool = False
    default: JsonValue = None
    type: str | None = Field(default=None, exclude_if=lambda v: v is None)
    items: str | None = Field(default=None, exclude_if=lambda v: v is None)
    choices: list[str] | None = Field(default=None, exclude_if=lambda v: v is None)
    help: str | None = Field(default=None, exclude_if=lambda v: v is None)
    positional: bool = False
    keyword_only: bool = False
    allow_repeating: bool = False


class CommandContractSchema(Model):
    command: str
    summary: str
    description: str
    side_effects: str
    requires_confirm: bool
    examples: list[str]
    parameters: list[ParameterSchema]
    session_options: list[ParameterSchema] = Field(default_factory=list)
    next_action_schema: NextActionSchema = Field(default_factory=NextActionSchema)


class CommandSummary(Model):
    command: str
    summary: str


class GroupIndexSchema(Model):
    type: str = "group_index"
    group: str
    commands: list[CommandSummary]
    session_options: list[ParameterSchema] = Field(default_factory=list)


class RootIndexSchema(Model):
    type: str = "command_index"
    groups: dict[str, list[CommandSummary]]
    session_options: list[ParameterSchema] = Field(default_factory=list)
    next_action_schema: NextActionSchema = Field(default_factory=NextActionSchema)


def _build_parameter_schema(arg: ArgumentSpec, full: bool) -> ParameterSchema:
    return ParameterSchema(
        name=arg.name,
        flags=arg.flags,
        negative_flags=arg.negative_flags,
        required=arg.required,
        default=arg.default,
        type=arg.type if full else None,
        items=arg.items if full else None,
        choices=arg.choices if full else None,
        help=arg.help if full else None,
        positional=arg.positional,
        keyword_only=arg.keyword_only,
        allow_repeating=arg.allow_repeating,
    )


def get_command_schema(path: str | None = None, full: bool = False) -> dict[str, JsonValue]:
    """生成指定路径或全局命令的强类型 Schema。"""
    registry = command_registry()
    session_options_specs = get_session_options_specs()
    global_session_options = [_build_parameter_schema(opt, full=full) for opt in session_options_specs]

    if not path:
        root_groups: dict[str, list[CommandSummary]] = {}
        for cmd_path, spec in registry.items():
            grp = cmd_path.split()[0]
            root_groups.setdefault(grp, []).append(CommandSummary(command=cmd_path, summary=spec.summary))
        return RootIndexSchema(groups=root_groups, session_options=global_session_options).model_dump()

    path = path.strip()

    if path in registry:
        spec = registry[path]
        params = [_build_parameter_schema(arg, full=full) for arg in spec.arguments]
        cmd_session_options = [_build_parameter_schema(opt, full=full) for opt in spec.session_options]
        contract = CommandContractSchema(
            command=spec.path,
            summary=spec.summary,
            description=spec.description,
            side_effects=spec.side_effects,
            requires_confirm=spec.requires_confirm,
            examples=spec.examples,
            parameters=params,
            session_options=cmd_session_options,
        )
        return contract.model_dump()

    # 路径匹配组
    matching = [spec for cmd_path, spec in registry.items() if cmd_path.startswith(path + " ")]
    if matching:
        group_cmds = [CommandSummary(command=s.path, summary=s.summary) for s in matching]
        return GroupIndexSchema(group=path, commands=group_cmds, session_options=global_session_options).model_dump()

    raise KeyError(f"未知的命令路径: {path}")


__all__ = [
    "ArgumentSpec",
    "COMMAND_CONTRACTS",
    "CommandContractSchema",
    "CommandSpec",
    "CommandSummary",
    "GroupIndexSchema",
    "NEXT_ACTION_SCHEMA",
    "NextActionFieldSchema",
    "NextActionSchema",
    "ParameterSchema",
    "RootIndexSchema",
    "command_registry",
    "get_command_schema",
    "get_command_spec",
    "get_session_options_specs",
]
