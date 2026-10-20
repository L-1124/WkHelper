"""CLI 命令规范定义与 Schema 自省。

命令树信息全部通过 Cyclopts 公开 API（`iter`/`__getitem__`/`assemble_argument_collection`）
获取，不依赖私有属性；摘要与描述以 `App.help` 和 docstring 为唯一真相，
`COMMAND_CONTRACTS` 只补充无法从代码推导的契约字段（副作用、确认要求、示例）。
"""

import inspect
import types
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from typing import Any, Literal, Union, get_args, get_origin

from cyclopts import App

from wkhelper.core.json_types import JsonDict, JsonValue


@dataclass
class ArgumentSpec:
    name: str
    flags: list[str]
    type: str = ""
    required: bool = False
    default: JsonValue = None
    help: str = ""
    choices: list[str] | None = None


@dataclass
class CommandSpec:
    path: str
    summary: str
    description: str
    arguments: list[ArgumentSpec] = field(default_factory=list)
    side_effects: str = "none"
    requires_confirm: bool = False
    examples: list[str] = field(default_factory=list)


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
        "description": "关闭 exam start --browser 拉起的常驻考场浏览器会话。",
        "side_effects": "local_write",
        "examples": ["wkhelper exam close"],
    },
    "exam answer": {
        "description": (
            "提交考试单题作答（写入服务端暂存，不改动最终得分）；支持 --delay 与 --delay-jitter。"
            "建议依据题目难度（判断单选 5~15s，多选/计算 15~35s，主观题 45~90s）动态指定思考延迟，避免高频机械提交触发风控。"
        ),
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper exam answer --course 1001 --exam 2001 --question 3001 --answer A --dry-run",
            "wkhelper exam answer --course 1001 --exam 2001 --question 3001 --answer A --delay 10 --delay-jitter 3 --confirm",
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
        "description": "显式确认后向平台发起进入考场请求，启动考场会话与授权令牌。",
        "side_effects": "remote_write",
        "requires_confirm": True,
        "examples": [
            "wkhelper exam start --course 1001 --exam 2001 --dry-run",
            "wkhelper exam start --course 1001 --exam 2001 --confirm",
        ],
    },
}


def _unwrap_optional(hint: Any) -> Any:
    """剥离 `Optional[T]`，返回 `T`。"""
    if get_origin(hint) in (Union, types.UnionType):
        args = [item for item in get_args(hint) if item is not type(None)]
        if args:
            return args[0]
    return hint


def _resolve_type_str(hint: Any) -> str:
    """从 Python 类型注解推导契约规范中的类型字符串。"""
    hint = _unwrap_optional(hint)
    origin = get_origin(hint)
    if origin in (list, Sequence, tuple, set):
        return "string"
    if hint is bool:
        return "bool"
    if hint is int:
        return "int"
    if hint is float:
        return "float"
    return "string"


def _resolve_choices(hint: Any) -> list[str] | None:
    """从 `Literal[...]` 注解推导可选值列表。"""
    hint = _unwrap_optional(hint)
    if get_origin(hint) is Literal:
        return [str(item) for item in get_args(hint)]
    return None


def _inspect_command_arguments(cmd_app: App) -> list[ArgumentSpec]:
    """从 Cyclopts 命令应用中内省解析参数列表。"""
    args_col = cmd_app.assemble_argument_collection()
    arg_specs: list[ArgumentSpec] = []
    for arg in args_col:
        name = arg.field_info.names[0]
        if arg.parameter.name:
            if isinstance(arg.parameter.name, (tuple, list)):
                flags = list(arg.parameter.name)
            else:
                flags = [str(arg.parameter.name)]
        else:
            flags = [f"--{name.replace('_', '-')}"]

        required = bool(arg.field_info.required)
        default = None if required or arg.field_info.default is inspect._empty else arg.field_info.default
        help_str = arg.parameter.help or arg.field_info.help or ""
        type_str = _resolve_type_str(arg.hint)

        choices: list[str] | None = None
        if arg.parameter.choices:
            choices = [str(c) for c in arg.parameter.choices]
        else:
            choices = _resolve_choices(arg.hint)

        arg_specs.append(
            ArgumentSpec(
                name=name,
                flags=flags,
                type=type_str,
                required=required,
                default=default,
                help=help_str,
                choices=choices,
            )
        )
    return arg_specs


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

    return CommandSpec(
        path=path,
        summary=summary,
        description=description,
        arguments=arguments,
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
            type="",
            required=arg.required,
            default=arg.default,
            help="",
            choices=None,
        )
        for arg in spec.arguments
    ]
    return replace(spec, arguments=compact_args)


@dataclass
class NextActionFieldSchema:
    type: str
    description: str
    items: str | None = None

    def to_dict(self) -> JsonDict:
        d: JsonDict = {"type": self.type, "description": self.description}
        if self.items:
            d["items"] = self.items
        return d


@dataclass
class NextActionSchema:
    """NextAction 专属的契约 Schema。"""

    type: str = "object"
    description: str = "供 AI Agent 下一步自引导决策的结构化命令动作"
    properties: dict[str, NextActionFieldSchema] = field(
        default_factory=lambda: {
            "reason": NextActionFieldSchema(type="string", description="执行该动作的意图与业务理由"),
            "argv": NextActionFieldSchema(type="array", items="string", description="完整的命令行执行参数列表"),
            "description": NextActionFieldSchema(type="string", description="可选的详细动作解释"),
        }
    )
    required: list[str] = field(default_factory=lambda: ["reason", "argv"])

    def to_dict(self) -> JsonDict:
        return {
            "type": self.type,
            "description": self.description,
            "properties": {k: v.to_dict() for k, v in self.properties.items()},
            "required": self.required,
        }


NEXT_ACTION_SCHEMA = NextActionSchema()


@dataclass
class ParameterSchema:
    name: str
    flags: list[str]
    required: bool = False
    default: JsonValue = None
    type: str | None = None
    help: str | None = None
    choices: list[str] | None = None

    def to_dict(self) -> JsonDict:
        d: JsonDict = {
            "name": self.name,
            "flags": self.flags,
            "required": self.required,
            "default": self.default,
        }
        if self.type is not None:
            d["type"] = self.type
        if self.help is not None:
            d["help"] = self.help
        if self.choices is not None:
            d["choices"] = self.choices
        return d


@dataclass
class CommandContractSchema:
    command: str
    summary: str
    description: str
    side_effects: str
    requires_confirm: bool
    examples: list[str]
    parameters: list[ParameterSchema]
    next_action_schema: NextActionSchema = field(default_factory=NextActionSchema)

    def to_dict(self) -> JsonDict:
        return {
            "command": self.command,
            "summary": self.summary,
            "description": self.description,
            "side_effects": self.side_effects,
            "requires_confirm": self.requires_confirm,
            "examples": self.examples,
            "parameters": [p.to_dict() for p in self.parameters],
            "next_action_schema": self.next_action_schema.to_dict(),
        }


@dataclass
class CommandSummary:
    command: str
    summary: str

    def to_dict(self) -> JsonDict:
        return {"command": self.command, "summary": self.summary}


@dataclass
class GroupIndexSchema:
    group: str
    commands: list[CommandSummary]
    type: str = "group_index"

    def to_dict(self) -> JsonDict:
        return {
            "type": self.type,
            "group": self.group,
            "commands": [c.to_dict() for c in self.commands],
        }


@dataclass
class RootIndexSchema:
    groups: dict[str, list[CommandSummary]]
    next_action_schema: NextActionSchema = field(default_factory=NextActionSchema)
    type: str = "command_index"

    def to_dict(self) -> JsonDict:
        return {
            "type": self.type,
            "groups": {k: [c.to_dict() for c in v] for k, v in self.groups.items()},
            "next_action_schema": self.next_action_schema.to_dict(),
        }


def get_command_schema(path: str | None = None, full: bool = False) -> JsonDict:
    """生成指定路径或全局命令的强类型 Schema。"""
    registry = command_registry()
    if not path:
        root_groups: dict[str, list[CommandSummary]] = {}
        for cmd_path, spec in registry.items():
            grp = cmd_path.split()[0]
            root_groups.setdefault(grp, []).append(CommandSummary(command=cmd_path, summary=spec.summary))
        return RootIndexSchema(groups=root_groups).to_dict()

    path = path.strip()
    if path in registry:
        spec = registry[path]
        params = [
            ParameterSchema(
                name=arg.name,
                flags=arg.flags,
                required=arg.required,
                default=arg.default,
                type=arg.type if full else None,
                help=arg.help if full else None,
                choices=arg.choices if full else None,
            )
            for arg in spec.arguments
        ]
        contract = CommandContractSchema(
            command=spec.path,
            summary=spec.summary,
            description=spec.description,
            side_effects=spec.side_effects,
            requires_confirm=spec.requires_confirm,
            examples=spec.examples,
            parameters=params,
        )
        return contract.to_dict()

    # 路径匹配组
    matching = [spec for cmd_path, spec in registry.items() if cmd_path.startswith(path + " ")]
    if matching:
        group_cmds = [CommandSummary(command=s.path, summary=s.summary) for s in matching]
        return GroupIndexSchema(group=path, commands=group_cmds).to_dict()

    raise KeyError(f"未知的命令路径: {path}")
