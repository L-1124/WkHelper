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
        "description": "查看本地环境配置、数据目录及已保存的账号列表。",
        "examples": ["wkhelper status"],
    },
    "schema": {
        "description": "查看 CLI 命令规范、参数定义、副作用与调用示例。",
        "examples": [
            "wkhelper schema",
            "wkhelper schema 'video learn'",
            "wkhelper schema 'video learn' --full",
        ],
    },
    "auth list": {
        "description": "列出本地已保存的账号列表。",
        "examples": ["wkhelper auth list", "wkhelper auth list --platform ykt"],
    },
    "auth status": {
        "description": "向平台发送轻量请求，验证指定账号的凭据是否有效。",
        "examples": [
            "wkhelper auth status --platform ykt",
            "wkhelper auth status --platform ykt --account 123456",
        ],
    },
    "auth login": {
        "description": "通过浏览器或导入 Cookie 登录账号，并加密保存凭据。",
        "side_effects": "local_write",
        "examples": [
            "wkhelper auth login --platform ykt",
            "wkhelper auth login --platform ykt --method browser",
            "wkhelper auth login --platform xtzx --method browser",
            "wkhelper auth login --platform icourse163 --method browser",
            "wkhelper auth login --platform ykt --method cookie --cookie-file cookies.json",
        ],
    },
    "auth switch": {
        "description": "切换默认账号及其所属平台，仅修改本地设置，不进行网络验证。",
        "side_effects": "local_write",
        "examples": [
            "wkhelper auth switch --platform ykt",
            "wkhelper auth switch --platform icourse163 --account 123456",
        ],
    },
    "auth remove": {
        "description": "删除本地已保存的账号凭据与元数据。",
        "side_effects": "local_delete",
        "requires_confirm": True,
        "examples": ["wkhelper auth remove --platform ykt --account 123456 --confirm"],
    },
    "auth storage status": {
        "description": "查看操作系统 Keyring 凭据存储后端的状态。",
        "examples": ["wkhelper auth storage status"],
    },
    "course list": {
        "description": "查询当前账号下的在学课程列表。",
        "examples": ["wkhelper course list --platform ykt", "wkhelper course list --limit 5"],
    },
    "course inspect": {
        "description": "查看指定课程的详细信息与大纲。",
        "examples": ["wkhelper course inspect --course 1001 --platform ykt"],
    },
    "video list": {
        "description": "查询课程的视频任务列表、完成状态与时长。",
        "examples": ["wkhelper video list --course 1001 --status pending"],
    },
    "video inspect": {
        "description": "查看单个视频在服务端的观看进度与参数边界。",
        "examples": ["wkhelper video inspect --course 1001 --video 2001"],
    },
    "video learn": {
        "description": "向平台发送心跳推进视频学习进度，支持单/多视频或计划文件。",
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper video learn --course 1001 --video 2001 --dry-run",
            "wkhelper video learn --course 1001 --video 2001 --confirm",
            "wkhelper video learn --plan-file plan.json --confirm",
        ],
    },
    "video snapshot": {
        "description": ("使用浏览器对指定视频进行抽帧截图，支持指定时间戳、步长范围或均匀采样。"),
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
        "description": "查询指定课程的作业列表与完成状态。",
        "examples": ["wkhelper homework list --course 1001 --status pending"],
    },
    "homework inspect": {
        "description": "查看作业详情、题目总数及题型分布。",
        "examples": ["wkhelper homework inspect --course 1001 --homework 3001"],
    },
    "homework submit": {
        "description": ("批量提交作业答案；支持 --delay、--delay-jitter 与 --random-order 参数控制提交间隔与次序。"),
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            'wkhelper homework submit -c 1001 -w 3001 --answers \'{"4001": ["A"]}\' --delay 8 --delay-jitter 3 --random-order --confirm',
            "wkhelper homework submit --course 1001 --homework 3001 --answers-file ans.json --confirm",
        ],
    },
    "question list": {
        "description": "分页查看作业题目摘要、选项与状态。",
        "examples": ["wkhelper question list --course 1001 --homework 3001 --limit 5"],
    },
    "question get": {
        "description": "查看单道题目的完整题干、公式、选项与附件。",
        "examples": ["wkhelper question get --course 1001 --homework 3001 --question 4001"],
    },
    "question submit": {
        "description": ("提交作业中的单道题目答案并返回判题结果；支持 --delay 与 --delay-jitter 控制提交等待。"),
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper question submit --course 1001 --homework 3001 --question 4001 --answer A --dry-run",
            "wkhelper question submit --course 1001 --homework 3001 --question 4001 --answer A --delay 8 --confirm",
            "wkhelper question submit --course 1001 --homework 3001 --question 4001 --answer A,B --confirm",
        ],
    },
    "article list": {
        "description": "查询指定课程的资料与图文任务列表及完成状态。",
        "examples": ["wkhelper article list --course 1001 --status pending"],
    },
    "article read": {
        "description": "将指定的资料/图文节点标记为已读。",
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper article read --course 1001 --article 2001 --dry-run",
            "wkhelper article read --course 1001 --article 2001 --confirm",
        ],
    },
    "discussion list": {
        "description": "查询指定课程的讨论题列表及回复状态。",
        "examples": ["wkhelper discussion list --course 1001 --status pending"],
    },
    "discussion inspect": {
        "description": "查看讨论题详情与教师要求。",
        "examples": ["wkhelper discussion inspect --course 1001 --discussion 3001"],
    },
    "discussion reply": {
        "description": "发表讨论回复完成考核任务。",
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper discussion reply --course 1001 --discussion 3001 --content '心得体会...' --dry-run",
            "wkhelper discussion reply --course 1001 --discussion 3001 --content '心得体会...' --confirm",
        ],
    },
    "discussion delete": {
        "description": "删除已发表的讨论回复。",
        "side_effects": "remote_delete",
        "requires_confirm": True,
        "examples": [
            "wkhelper discussion delete --course 1001 --comment 8888 --dry-run",
            "wkhelper discussion delete --course 1001 --comment 8888 --confirm",
        ],
    },
    "notice list": {
        "description": "查询指定课程的公告与通知列表及已读状态。",
        "examples": ["wkhelper notice list --course 1001 --status pending"],
    },
    "notice inspect": {
        "description": "查看公告正文、发布者与发布时间。",
        "examples": ["wkhelper notice inspect --course 1001 --notice 4001"],
    },
    "exam list": {
        "description": "查询指定课程的期中/期末考试列表与截止时间。",
        "examples": ["wkhelper exam list --course 1001"],
    },
    "exam inspect": {
        "description": "查看考试封面、考试须知、分值与考场状态。",
        "examples": ["wkhelper exam inspect --course 1001 --exam 2001"],
    },
    "exam status": {
        "description": "检查考场接口与暂存状态 (只读诊断)。",
        "examples": ["wkhelper exam status --course 1001 --exam 2001"],
    },
    "exam result": {
        "description": "查看考场成绩与作答结果 (只读)。",
        "examples": ["wkhelper exam result --course 1001 --exam 2001"],
    },
    "exam close": {
        "description": "关闭 exam start 打开的考场浏览器会话并清理临时状态。",
        "side_effects": "local_write",
        "examples": ["wkhelper exam close"],
    },
    "exam answer": {
        "description": (
            "在已打开的考场浏览器中作答单道题目，由考场前端自动暂存。"
            "需先执行 `exam start --confirm` 打开考场浏览器；强监考考试拒绝执行。"
            "客观题传选项键 (多选用逗号分隔)，填空题按空用逗号分隔，主观题整段填入。"
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
        "description": "将考场试卷导出为 Markdown 或 JSON 文件，支持下载配图。",
        "side_effects": "local_file_write",
        "examples": [
            "wkhelper exam export --course 1001 --exam 2001 --output ./exam.md",
            "wkhelper exam export --course 1001 --exam 2001 --output ./exam.json",
            "wkhelper exam export --course 1001 --exam 2001 --output ./exam_dump --format json",
        ],
    },
    "exam paper": {
        "description": "查看考场试卷题目 (只读)。",
        "examples": ["wkhelper exam paper --course 1001 --exam 2001"],
    },
    "exam start": {
        "description": (
            "打开考场浏览器窗口；传入 `--begin` 在浏览器中自动点击开始答题并启动计时。"
            "`--dry-run` 仅预览考场信息，不实际进入；强监考考试拒绝进入。"
        ),
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper exam start --course 1001 --exam 2001 --dry-run",
            "wkhelper exam start --course 1001 --exam 2001 --confirm",
            "wkhelper exam start --course 1001 --exam 2001 --begin --confirm",
        ],
    },
    "questions export": {
        "description": "将课程全部作业题目导出为 Markdown 或 JSON 文件，支持 --only-correct-answers 仅保留标准答案。",
        "side_effects": "local_file_write",
        "examples": [
            "wkhelper questions export --course 1001 --output ./questions.md",
            "wkhelper questions export --course 1001 --output ./questions.json",
            "wkhelper questions export --course 1001 --output ./questions.json --only-correct-answers",
            "wkhelper questions export --course 1001 --output ./questions.md --only-correct-answers",
            "wkhelper questions export --course 1001 --output ./dump --format json",
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
            hidden.update(flags)
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
