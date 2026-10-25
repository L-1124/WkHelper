# PROJECT KNOWLEDGE BASE

**Updated:** 2026-10-25
**Branch:** main

## OVERVIEW

`wkhelper` 是面向 AI Agent 的非交互网课自动化 CLI 工具，支持雨课堂与学堂在线。

- 平台：`yuketang.cn` / `xuetangx.com`
- 架构：异步（`asyncio` + `curl_cffi` + `TaskGroup`）+ 系统凭据存储（`keyring`）
- 交互：标准单行 JSON 信封（stdout）+ 结构化 NDJSON 事件（stderr）
- 命令框架：Cyclopts 5.x 声明式类型化命令树（根应用统一 `help_formatter="plain"`，无 ANSI；全局会话参数由 meta 应用承载）

业务流程按 `video` / `homework` / `exam` / `article` / `discussion` / `notice` 组织；`domain` 只放数据与规则，`infra` 放可复用机制，`platform` 放平台协议，`cli` 放命令与展示。
HTTP 会话统一由 `infra/http.build_session()` 构建；本地 CDP 明确关闭代理与指纹伪装，业务代码不得直接构造客户端。

## STRUCTURE

```text
wkhelper/
  main.py                   # 进程入口：异常到退出码映射、同步/异步两条薄封装
  cli/
    app.py                  # 根 App 装配 + meta 应用（全局会话参数）
    context.py              # ExecutionContext 与 OutputFormat/PlatformCode 等类型契约
    deps.py                 # 平台打开、分页、载荷解析等命令共享依赖
    envelope.py             # CommandResult / 信封 / NextAction
    markdown.py             # 终端 Markdown 渲染
    output.py               # 统一输出（信封/错误）与 unified_result_action
    spec.py                 # 命令规范注册表与 schema 自省
    commands/               # 按业务域拆分的子应用与命令函数
  domain/                   # 按账号、课程、视频、作业、内容、考试拆分的数据模型
    model.py                # 领域、输出、配置与持久化数据的 Pydantic 公共配置
    validation.py           # CLI 输入校验异常的脱敏边界
    questions.py            # 题型与答案归一规则
  infra/
    http.py                 # 唯一会话构造出口、HTTP 请求与重试
    clock.py                # 服务器时钟校准
    credentials.py          # Keyring 秘密 + SQLite 元数据
    events.py               # NDJSON 事件与 notifier
    serialization.py        # Pydantic TypeAdapter JSON 编码边界
    files.py                # 原子拒绝覆盖发布
    browser/                # Chromium 进程与 CDP 通用机制
  auth/                     # Cookie 解析与官方页面 CDP 浏览器登录编排
  video/                    # 计划、引擎、编排、业务服务、抽帧与热池
    scripts/                # 就绪、Canvas 抽帧、截图回退页面资源
  homework/                 # 作业提交、编排与业务服务
  exam/                     # 考试列表、浏览器作答、会话、监考与导出
    scripts/                # 开始、就绪、作答页面资源
  article/                  # 资料列表与完成业务
  discussion/               # 讨论列表、详情与提交业务
  notice/                   # 公告列表业务
  exporting/                # 作业导出、共享资源下载与 Markdown 转换
  platform/                 # BasePlatform 公开协议、章节解析、两平台适配与字体资源

tests/
  cli/                      # 命令规范、信封、入口流程
  core/                     # 凭据、提交、视频引擎、计划、导出
  platform/                 # 平台适配

其他文件：README.md（用户文档）、AGENTS.md（本文件）、pyproject.toml
```

## WHERE TO CHANGE

| 需求              | 主要文件                          | 说明                         |
|-------------------|-----------------------------------|------------------------------|
| 调整视频学习策略  | `wkhelper/video/engine.py`        | 心跳发送、限流重试、终态校准 |
| 调整计划编排流程  | `wkhelper/video/runner.py`        | 目标去重、参数分层、断点续学 |
| 平台 API 适配     | `wkhelper/platform/*.py`          | 只写平台协议，不导入业务引擎或 runner |
| CLI 契约与 Schema | `wkhelper/cli/spec.py`             | 命令规范与渐进披露 |
| 输出契约与数据模型| `wkhelper/domain/*.py`             | 按业务归属；输入计划在 `video/plan.py` |
| 凭据存储策略      | `wkhelper/infra/credentials.py`    | 系统 Keyring 与元数据分离 |

## CONVENTIONS

- **必须使用 `uv`**：
  - `uv sync`
  - `uv run wkhelper`
  - `uv run pytest -q`
- 代码风格：
  - 注释与 docstring 使用中文
  - 统一现代类型标注（`list[str]`、`dict[str, Any]`），使用 PEP 695 语法（`type X = ...`、`class Foo[T]`）
  - **禁止** `from __future__ import annotations`
- 类型策略：
  - 动态 JSON 使用 `pydantic.JsonValue` / `dict[str, JsonValue]`；禁止自建递归别名、以 `cast` 假装完成运行时校验
  - 领域、信封、Schema、配置和持久化会话使用 `domain.model.Model`；`Field` / validator / computed_field 声明默认值、兼容归一与派生字段
  - `Any` 仅用于平台原始载荷、HTTP 附加参数与统一编码边界；持有进程句柄的 `BrowserProcess` 保留 dataclass
  - 导出与字体反混淆的资源下载走 `infra.http.Fetcher`（实现为 `BasePlatform.fetch_bytes`）
  - CLI 命令返回 `CommandResult[...]`，不直接打印；序列化只经 `unified_result_action`
  - JSON 编码统一用 `infra.serialization.encode_json`，展开用 `to_json_value`；不手写递归转换或字符串兜底
  - 使用 `model_validate` / `model_validate_json` 和 `model_dump` / `model_dump_json`，不保留 `to_dict` / `from_dict` 兼容入口；更新模型需重新校验，禁止 `model_copy(update=...)` 绕过约束
- 错误处理：
  - 库代码不要 `exit(1)`，抛出业务异常
  - 退出码：2 参数错误、3 认证缺失、4 目标不存在、5 前置条件不满足、6 平台失败、7 部分失败、1 内部错误
  - Pydantic `ValidationError` 只在 CLI 用户输入边界转为脱敏 `ValueError`；内部数据契约失败不得误报参数错误
- CLI 参数契约：
  - 全局会话参数只在 `cli/app.py` 的 `app.meta.default` 上声明，命令层不要重复定义或手工扫描 `sys.argv`
  - 枚举型选项用 `Literal[...]`（`PlatformCode` 等复用 `cli/context.py` 的类型别名），校验与 `[choices: ...]` 帮助由 Cyclopts 自动生成，不要另写 `if x not in (...)` 校验
  - `console`/`help_formatter`/`exit_on_error`/`print_error` 只在根应用设置，子应用继承
  - 命令树内省只用公开 API（`iter(app)`、`app[name]`、`assemble_argument_collection()`），禁止访问 `_commands` 等私有属性
- 架构约束：
  - 业务编排只在对应业务包；平台基类不持有列表投影与业务执行方法，不保留旧 `core` 路径兼容层
  - `domain` 不导入 CLI、平台或业务服务；协议字段放 `platform/protocol_types.py`，浏览器页面逻辑放各业务 `scripts/`
  - 事件派发统一用 `infra.events.notifier`；平台调用传输机制经 `infra.http`，不复制重试或时钟实现
  - CLI 不得访问平台私有方法（用 `BasePlatform` 公开方法）
  - 命令层通过 `_open_platform` / `_open_course` 获取已认证平台，不要各自复制会话与登录样板

## ANTI-PATTERNS

- 在平台适配层复制业务流程，或把不同职责重新集中到一个杂物模块
- 在异步流程中调用阻塞式交互或嵌套 `asyncio.run`
- 用 `Any` / `object` 逃逸输出契约检查，或把不可序列化对象塞进输出模型
- 在 `cli/app.py` 中手工拼 JSON 或重复分页/参数校验逻辑
- 手写 `argv` 扫描器替代 Cyclopts 的参数解析（全局参数属 meta 应用）
- 访问 Cyclopts 私有属性（`App._commands` 等）做命令树内省

## COMMANDS

```bash
# 运行
uv run wkhelper

# 质量检查
uv run ruff check . --fix
uv run ruff format .
uv run pyrefly check

# 测试
uv run pytest -q
```

## NOTES

- 计划契约见 `video/plan.py::VideoPlan.model_validate`：严格拒绝未知字段、布尔整数与非有限数；CLI 边界经 `validate_input` 转为不含输入值的参数错误，禁止链接原始校验异常泄露 Cookie。
- 叶节点分类以平台前端为准：`5=考试`、`6=作业`、`7=课件`、`8=线下课堂`、`9=随堂测验`、`10=公告`；协议与实测见 `.agents/work/protocol-js-audit.md`。
- 导出目标已存在时返回 `FILE_EXISTS`（退出码 5），不覆盖既有文件。
- 全局 `--cookie-file` 仅与 `--credential-mode session` 搭配，且必须显式 `--platform`；置于子命令之后时归属该命令的同名选项（如 `auth login --cookie-file`）。
- 入口与 `dispatch()` 用 Cyclopts 公开 `parse_args()` 完成同步解析，再执行命令协程并显式调用 `unified_result_action`；不得让 Cyclopts 的共享调用栈跨越 `await`。
- 全局配置由不可变 `SessionOptions` 声明，`async_main()` 用 `execution_context()` 建立调用级 `ContextVar` 作用域，异常、取消与嵌套调用均恢复外层状态；通过 `get_context()` 访问，不保留全局可变 `ctx`。
