# PROJECT KNOWLEDGE BASE

**Updated:** 2026-10-18
**Branch:** main

## OVERVIEW

`wkhelper` 是面向 AI Agent 的非交互网课自动化 CLI 工具，支持雨课堂与学堂在线。

- 平台：`yuketang.cn` / `xuetangx.com`
- 架构：异步（`asyncio` + `curl_cffi` + `TaskGroup`）+ 系统凭据存储（`keyring`）
- 交互：标准单行 JSON 信封（stdout）+ 结构化 NDJSON 事件（stderr）
- 命令框架：Cyclopts 5.x 声明式类型化命令树（根应用统一 `help_formatter="plain"`，无 ANSI；全局会话参数由 meta 应用承载）

核心思路是把通用流程放在 `wkhelper/core`，平台差异放在 `wkhelper/platform`，命令规范与自省放在 `wkhelper/cli`。
HTTP 会话统一由 `core/transport.build_session()` 构建（curl-impersonate 浏览器指纹伪装），业务代码不得直接实例化客户端。

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
  core/
    base_platform.py        # 平台抽象接口与模板方法（含章节树遍历）
    config.py               # 平台协议常量与并发上限
    credential_store.py     # Keyring 秘密 + SQLite 元数据
    deobfuscator.py         # 字体反混淆（动态字体结构）
    entities.py             # 跨平台领域实体（课程/作业/题目/账号）
    events.py               # NDJSON 事件通道与 notifier
    exceptions.py           # 业务异常
    exporter.py             # Markdown 原子导出（拒绝覆盖）
    homework.py             # 作业并发提交流程
    homework_runner.py      # 作业提交编排（事件契约）
    json_types.py           # JsonValue/JsonDict 递归契约与收窄工具
    plan.py                 # 视频计划与执行参数模型
    results.py              # 命令输出契约模型
    transport.py            # Fetcher：按 URL 取字节的传输抽象
    video.py                # 参数化视频引擎
    video_runner.py         # 视频计划编排、预算与检查点
  platform/
    login_helper.py         # 临时二维码与本地回环 Web 展示
    xuetangx.py             # 学堂在线适配
    yuketang.py             # 雨课堂适配

tests/
  cli/                      # 命令规范、信封、入口流程
  core/                     # 凭据、提交、视频引擎、计划、导出
  platform/                 # 平台适配

其他文件：README.md（用户文档）、AGENTS.md（本文件）、pyproject.toml
```

## WHERE TO CHANGE

| 需求              | 主要文件                          | 说明                         |
|-------------------|-----------------------------------|------------------------------|
| 调整视频学习策略  | `wkhelper/core/video.py`          | 心跳发送、限流重试、终态校准 |
| 调整计划编排流程  | `wkhelper/core/video_runner.py`   | 目标去重、参数分层、断点续学 |
| 平台 API 适配     | `wkhelper/platform/*.py`          | 仅写平台差异（含 `heartbeat_profile`），不复制通用流程 |
| CLI 契约与 Schema | `wkhelper/cli/spec.py`            | 命令规范与渐进披露           |
| 输出契约与数据模型| `wkhelper/core/results.py`        | 命令结果模型；实体见 `entities.py`，计划见 `plan.py` |
| 凭据存储策略      | `wkhelper/core/credential_store.py`| 系统 Keyring 与元数据分离   |

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
  - `JsonValue` 是递归联合（`str | int | float | bool | None | Sequence | Mapping`），**不要**退化成 `Any`；JSON 解析边界用一次 `cast` 收窄
  - 输出/编排层（信封、事件、Schema、命令结果）只用精确模型与 `JsonValue` / `JsonDict`
  - `Any` 仅用于平台原始载荷（HTTP JSON、章节树、字体结构）与 HTTP 附加参数等动态边界
  - `core` 不直接持有 HTTP 客户端：资源下载走 `core.transport.Fetcher`（实现为 `BasePlatform.fetch_bytes`）
  - CLI 命令返回 `CommandResult[...]`，不直接打印；序列化只经 `unified_result_action`
  - JSON 编码统一用 `msgspec.json.encode`（支持 dataclass 嵌套）；不要手写递归序列化
  - 需要保留缩进的文件（检查点）先用 `msgspec.to_builtins` 展开再 `json.dumps(indent=2)`
- 错误处理：
  - 库代码不要 `exit(1)`，抛出业务异常
  - 退出码：2 参数错误、3 认证缺失、4 目标不存在、5 前置条件不满足、6 平台失败、7 部分失败、1 内部错误
- CLI 参数契约：
  - 全局会话参数只在 `cli/app.py` 的 `app.meta.default` 上声明，命令层不要重复定义或手工扫描 `sys.argv`
  - 枚举型选项用 `Literal[...]`（`PlatformCode` 等复用 `cli/context.py` 的类型别名），校验与 `[choices: ...]` 帮助由 Cyclopts 自动生成，不要另写 `if x not in (...)` 校验
  - `console`/`help_formatter`/`exit_on_error`/`print_error` 只在根应用设置，子应用继承
  - 命令树内省只用公开 API（`iter(app)`、`app[name]`、`assemble_argument_collection()`），禁止访问 `_commands` 等私有属性
- 架构约束：
  - 通用流程放 `core`
  - 平台模块只写平台 API 细节；协议固定字段用 `HeartbeatProfile` 之类显式声明，不要在 core 里写平台常量
  - 事件派发统一用 `core.events.notifier`，不要各自实现 `_notify` 分支
  - CLI 不得访问平台私有方法（用 `BasePlatform` 公开方法）
  - 命令层通过 `_open_platform` / `_open_course` 获取已认证平台，不要各自复制会话与登录样板

## ANTI-PATTERNS

- 在平台适配层复制通用逻辑（应下沉到 `core`）
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

- 计划文件契约见 `core/plan.py::VideoPlan.from_mapping`；未知字段一律报错。
- 叶节点分类以平台前端为准：`5=考试`、`6=作业`、`7=课件`、`8=线下课堂`、`9=随堂测验`、`10=公告`；协议与实测见 `.agents/work/protocol-js-audit.md`。
- 导出目标已存在时返回 `FILE_EXISTS`（退出码 5），不覆盖既有文件。
- 全局 `--cookie-file` 仅与 `--credential-mode session` 搭配，且必须显式 `--platform`；置于子命令之后时归属该命令的同名选项（如 `auth login --cookie-file`）。
- meta 启动器内对子命令的调用是嵌套调用，Cyclopts 会强制 `result_action="return_value"`，因此 `dispatch()` 必须显式调用 `unified_result_action` 完成渲染与退出码换算。
