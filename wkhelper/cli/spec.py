"""CLI 命令规范定义与 Schema 自省。"""

from dataclasses import dataclass, field

from wkhelper.core.models import JsonDict, JsonValue


@dataclass
class ArgumentSpec:
    name: str
    flags: list[str]
    type: str
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


COMMAND_REGISTRY: dict[str, CommandSpec] = {
    "status": CommandSpec(
        path="status",
        summary="查看本地环境与账号概况",
        description="读取本地配置、默认数据目录及已存储账号摘要（不进行网络认证请求）。",
        examples=["wkhelper status"],
    ),
    "schema": CommandSpec(
        path="schema",
        summary="CLI 契约自省",
        description="按层级查看命令规范、参数定义、副作用与示例。",
        arguments=[
            ArgumentSpec("path", ["path"], "string", required=False, default=None, help="命令路径，例如 'video learn'"),
            ArgumentSpec("full", ["--full"], "bool", required=False, default=False, help="展开完整参数与字段详情"),
        ],
        examples=["wkhelper schema", "wkhelper schema 'video learn'", "wkhelper schema 'video learn' --full"],
    ),
    "auth list": CommandSpec(
        path="auth list",
        summary="列出已存储的账号摘要",
        description="查询本地已保存的账号列表，绝不输出敏感 Cookie 或密码。",
        arguments=[
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台过滤"),
        ],
        examples=["wkhelper auth list", "wkhelper auth list --platform ykt"],
    ),
    "auth status": CommandSpec(
        path="auth status",
        summary="联网验证指定账号登录态",
        description="使用指定账号向平台发起轻量只读请求，检查凭据是否依然有效。",
        arguments=[
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=True, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID（若唯一可省略）"),
        ],
        examples=["wkhelper auth status --platform ykt", "wkhelper auth status --platform ykt --account 123456"],
    ),
    "auth login": CommandSpec(
        path="auth login",
        summary="账号登录",
        description="通过扫码或导入 Cookie 登录并保存至系统安全凭据管理器。",
        side_effects="local_write",
        arguments=[
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=True, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("method", ["--method", "-m"], "string", required=True, choices=["qrcode", "cookie"], help="登录方式"),
            ArgumentSpec("cookie_file", ["--cookie-file"], "string", required=False, help="Cookie JSON 文件路径（使用 - 从 stdin 读取）"),
            ArgumentSpec(
                "display", ["--display"], "string", required=False, default="image", choices=["image", "browser"], help="二维码呈现方式"
            ),
            ArgumentSpec(
                "open_browser", ["--open-browser"], "bool", required=False, default=False, help="是否自动调用系统浏览器打开本地二维码页"
            ),
            ArgumentSpec("timeout", ["--timeout"], "int", required=False, default=120, help="扫码登录超时秒数"),
        ],
        examples=[
            "wkhelper auth login --platform ykt --method qrcode",
            "wkhelper auth login --platform ykt --method qrcode --display browser --open-browser",
            "wkhelper auth login --platform ykt --method cookie --cookie-file cookies.json",
        ],
    ),
    "auth remove": CommandSpec(
        path="auth remove",
        summary="删除指定账号凭据",
        description="从系统凭据管理器和本地元数据中删除账号凭据。",
        side_effects="local_delete",
        requires_confirm=True,
        arguments=[
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=True, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=True, help="账号 ID"),
            ArgumentSpec("confirm", ["--confirm"], "bool", required=False, default=False, help="确认删除"),
        ],
        examples=["wkhelper auth remove --platform ykt --account 123456 --confirm"],
    ),
    "auth storage status": CommandSpec(
        path="auth storage status",
        summary="查看系统凭据存储后端状态",
        description="检查当前操作系统 keyring 后端类型与可用性。",
        examples=["wkhelper auth storage status"],
    ),
    "course list": CommandSpec(
        path="course list",
        summary="查询当前账号的课程列表",
        description="有界分页列出当前账号下的所有在学课程。",
        arguments=[
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID"),
            ArgumentSpec("limit", ["--limit"], "int", required=False, default=10, help="每页条目数 (1-100)"),
            ArgumentSpec("cursor", ["--cursor"], "string", required=False, help="分页游标"),
        ],
        examples=["wkhelper course list --platform ykt", "wkhelper course list --limit 5"],
    ),
    "course inspect": CommandSpec(
        path="course inspect",
        summary="查看指定课程的详细信息",
        description="查询指定课程的元数据与大纲概要。",
        arguments=[
            ArgumentSpec("course", ["--course", "-c"], "string", required=True, help="课程 ID"),
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID"),
        ],
        examples=["wkhelper course inspect --course 1001 --platform ykt"],
    ),
    "video list": CommandSpec(
        path="video list",
        summary="查询课程视频列表",
        description="有界分页列出指定课程中的视频叶子节点、完成状态与时长。",
        arguments=[
            ArgumentSpec("course", ["--course", "-c"], "string", required=True, help="课程 ID"),
            ArgumentSpec(
                "status", ["--status"], "string", required=False, default="all", choices=["pending", "completed", "all"], help="状态过滤"
            ),
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID"),
            ArgumentSpec("limit", ["--limit"], "int", required=False, default=10, help="每页条目数"),
            ArgumentSpec("cursor", ["--cursor"], "string", required=False, help="分页游标"),
        ],
        examples=["wkhelper video list --course 1001 --status pending"],
    ),
    "video inspect": CommandSpec(
        path="video inspect",
        summary="查询单视频进度与执行约束",
        description="查询服务端当前记录的视频播放进度、观看长度与可用参数边界。",
        arguments=[
            ArgumentSpec("course", ["--course", "-c"], "string", required=True, help="课程 ID"),
            ArgumentSpec("video", ["--video", "-v"], "string", required=True, help="视频 ID"),
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID"),
        ],
        examples=["wkhelper video inspect --course 1001 --video 2001"],
    ),
    "video learn": CommandSpec(
        path="video learn",
        summary="执行视频学习流程",
        description="向平台发送心跳推进学习进度，支持单/多视频或计划文件，全面开放控制参数。",
        side_effects="remote_write",
        requires_confirm=True,
        arguments=[
            ArgumentSpec("course", ["--course", "-c"], "string", required=False, help="课程 ID"),
            ArgumentSpec("video", ["--video", "-v"], "string", required=False, help="视频 ID（可多次指定）"),
            ArgumentSpec("plan_file", ["--plan-file"], "string", required=False, help="批量执行计划 JSON 文件"),
            ArgumentSpec("concurrency", ["--concurrency"], "int", required=False, default=1, help="并发任务数"),
            ArgumentSpec("learning_rate", ["--learning-rate"], "float", required=False, default=1.0, help="推进速率乘数"),
            ArgumentSpec("heartbeat_interval", ["--heartbeat-interval"], "float", required=False, default=1.5, help="心跳间隔（秒）"),
            ArgumentSpec("progress_interval", ["--progress-interval"], "float", required=False, default=15.0, help="进度复查间隔（秒）"),
            ArgumentSpec(
                "completion_threshold", ["--completion-threshold"], "float", required=False, default=1.0, help="完成进度阈值 (0-1.0]"
            ),
            ArgumentSpec("video_timeout", ["--video-timeout"], "float", required=False, default=1800.0, help="单视频总超时（秒）"),
            ArgumentSpec("total_timeout", ["--total-timeout"], "float", required=False, default=3600.0, help="批任务总超时（秒）"),
            ArgumentSpec("stall_timeout", ["--stall-timeout"], "float", required=False, default=180.0, help="无进展停滞超时（秒）"),
            ArgumentSpec("max_heartbeats", ["--max-heartbeats"], "int", required=False, default=1000, help="单视频最大心跳数"),
            ArgumentSpec(
                "on_error", ["--on-error"], "string", required=False, default="stop", choices=["stop", "continue"], help="出错策略"
            ),
            ArgumentSpec("checkpoint_file", ["--checkpoint-file"], "string", required=False, help="断点状态保存文件"),
            ArgumentSpec("resume_from", ["--resume-from"], "string", required=False, help="从检查点文件恢复执行"),
            ArgumentSpec("dry_run", ["--dry-run"], "bool", required=False, default=False, help="仅预览计划与参数，不发送心跳"),
            ArgumentSpec("confirm", ["--confirm"], "bool", required=False, default=False, help="确认执行写入操作"),
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID"),
        ],
        examples=[
            "wkhelper video learn --course 1001 --video 2001 --dry-run",
            "wkhelper video learn --course 1001 --video 2001 --confirm",
            "wkhelper video learn --plan-file plan.json --confirm",
        ],
    ),
    "homework list": CommandSpec(
        path="homework list",
        summary="查询课程作业列表",
        description="有界分页列出指定课程的作业、截止时间与完成状态。",
        arguments=[
            ArgumentSpec("course", ["--course", "-c"], "string", required=True, help="课程 ID"),
            ArgumentSpec(
                "status", ["--status"], "string", required=False, default="all", choices=["pending", "completed", "all"], help="状态过滤"
            ),
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID"),
            ArgumentSpec("limit", ["--limit"], "int", required=False, default=10, help="每页条目数"),
            ArgumentSpec("cursor", ["--cursor"], "string", required=False, help="分页游标"),
        ],
        examples=["wkhelper homework list --course 1001 --status pending"],
    ),
    "homework inspect": CommandSpec(
        path="homework inspect",
        summary="查看作业元数据及题目大纲",
        description="查询作业详情、题目总数及题目类型分布，不内嵌完整题干全文。",
        arguments=[
            ArgumentSpec("course", ["--course", "-c"], "string", required=True, help="课程 ID"),
            ArgumentSpec("homework", ["--homework", "-w"], "string", required=True, help="作业 ID"),
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID"),
        ],
        examples=["wkhelper homework inspect --course 1001 --homework 3001"],
    ),
    "homework submit": CommandSpec(
        path="homework submit",
        summary="提交作业答案",
        description="将外部 Agent 生成的显式答案文件提交到平台，支持 dry-run 校验。",
        side_effects="remote_write",
        requires_confirm=True,
        arguments=[
            ArgumentSpec("course", ["--course", "-c"], "string", required=True, help="课程 ID"),
            ArgumentSpec("homework", ["--homework", "-w"], "string", required=True, help="作业 ID"),
            ArgumentSpec("answers_file", ["--answers-file"], "string", required=False, help="答案 JSON 文件（使用 - 从 stdin 读取）"),
            ArgumentSpec("answers", ["--answers"], "string", required=False, help='直接传入 JSON 格式答案（例如 \'{"4001": ["A"]}\'）'),
            ArgumentSpec("dry_run", ["--dry-run"], "bool", required=False, default=False, help="校验题目与选项合法性，不提交"),
            ArgumentSpec("confirm", ["--confirm"], "bool", required=False, default=False, help="确认提交答案"),
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID"),
        ],
        examples=[
            'wkhelper homework submit --course 1001 --homework 3001 --answers \'{"4001": ["A"]}\' --confirm',
            "wkhelper homework submit --course 1001 --homework 3001 --answers-file ans.json --confirm",
        ],
    ),
    "question list": CommandSpec(
        path="question list",
        summary="分页获取作业题目",
        description="按页读取题目摘要、选项与修订版本号。",
        arguments=[
            ArgumentSpec("course", ["--course", "-c"], "string", required=True, help="课程 ID"),
            ArgumentSpec("homework", ["--homework", "-w"], "string", required=True, help="作业 ID"),
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID"),
            ArgumentSpec("limit", ["--limit"], "int", required=False, default=10, help="每页条数"),
            ArgumentSpec("cursor", ["--cursor"], "string", required=False, help="分页游标"),
        ],
        examples=["wkhelper question list --course 1001 --homework 3001 --limit 5"],
    ),
    "question get": CommandSpec(
        path="question get",
        summary="获取单题完整详情",
        description="获取单道题目的完整反混淆题干、公式、选项与附件引用。",
        arguments=[
            ArgumentSpec("course", ["--course", "-c"], "string", required=True, help="课程 ID"),
            ArgumentSpec("homework", ["--homework", "-w"], "string", required=True, help="作业 ID"),
            ArgumentSpec("question", ["--question", "-q"], "string", required=True, help="题目 ID"),
            ArgumentSpec("output", ["--output", "-o"], "string", required=False, help="超大题目内容输出文件路径"),
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID"),
        ],
        examples=["wkhelper question get --course 1001 --homework 3001 --question 4001"],
    ),
    "question submit": CommandSpec(
        path="question submit",
        summary="提交单道题目答案",
        description="直接向作业提交单道题目的答案选项并返回判题结果。",
        side_effects="remote_write",
        requires_confirm=True,
        arguments=[
            ArgumentSpec("course", ["--course", "-c"], "string", required=True, help="课程 ID"),
            ArgumentSpec("homework", ["--homework", "-w"], "string", required=True, help="作业 ID"),
            ArgumentSpec("question", ["--question", "-q"], "string", required=True, help="题目 ID"),
            ArgumentSpec("answer", ["--answer", "-a"], "string", required=True, help="答案选项 (例如 'A' 或 'A,B')"),
            ArgumentSpec("dry_run", ["--dry-run"], "bool", required=False, default=False, help="校验参数，不提交"),
            ArgumentSpec("confirm", ["--confirm"], "bool", required=False, default=False, help="确认提交答案"),
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account"], "string", required=False, help="账号 ID"),
        ],
        examples=[
            "wkhelper question submit --course 1001 --homework 3001 --question 4001 --answer A --dry-run",
            "wkhelper question submit --course 1001 --homework 3001 --question 4001 --answer A --confirm",
            "wkhelper question submit --course 1001 --homework 3001 --question 4001 --answer A,B --confirm",
        ],
    ),
    "questions export": CommandSpec(
        path="questions export",
        summary="导出课程题目为 Markdown",
        description="将指定课程的作业题干与选项完整导出为 Markdown 文件。",
        side_effects="local_file_write",
        arguments=[
            ArgumentSpec("course", ["--course", "-c"], "string", required=True, help="课程 ID"),
            ArgumentSpec("output", ["--output", "-o"], "string", required=True, help="导出 Markdown 文件路径"),
            ArgumentSpec("platform", ["--platform", "-p"], "string", required=False, choices=["ykt", "xtzx"], help="平台代码"),
            ArgumentSpec("account", ["--account", "-a"], "string", required=False, help="账号 ID"),
        ],
        examples=["wkhelper questions export --course 1001 --output ./export.md"],
    ),
}


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
    if not path:
        root_groups: dict[str, list[CommandSummary]] = {}
        for cmd_path, spec in COMMAND_REGISTRY.items():
            parts = cmd_path.split()
            grp = parts[0]
            if grp not in root_groups:
                root_groups[grp] = []
            root_groups[grp].append(CommandSummary(command=cmd_path, summary=spec.summary))
        return RootIndexSchema(groups=root_groups).to_dict()

    path = path.strip()
    if path in COMMAND_REGISTRY:
        spec = COMMAND_REGISTRY[path]
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
    matching = [spec for cmd_path, spec in COMMAND_REGISTRY.items() if cmd_path.startswith(path + " ")]
    if matching:
        group_cmds = [CommandSummary(command=s.path, summary=s.summary) for s in matching]
        return GroupIndexSchema(group=path, commands=group_cmds).to_dict()

    raise KeyError(f"未知的命令路径: {path}")
