# PROJECT KNOWLEDGE BASE

**Updated:** 2026-10-18
**Branch:** main

## OVERVIEW

`wkhelper` 是面向 AI Agent 的非交互网课自动化 CLI 工具，支持雨课堂与学堂在线。

- 平台：`yuketang.cn` / `xuetangx.com`
- 架构：异步（`asyncio` + `niquests` + `TaskGroup`）+ 系统凭据存储（`keyring`）
- 交互：标准单行 JSON 信封（stdout）+ 结构化 NDJSON 事件（stderr）
- 命令框架：Cyclopts 声明式类型化命令树（`help_formatter="plain"`，无 ANSI）

核心思路是把通用流程放在 `wkhelper/core`，平台差异放在 `wkhelper/platform`，命令规范与自省放在 `wkhelper/cli`。

## STRUCTURE

```text
wkhelper/
  main.py                   # 进程入口：全局参数、异常到退出码映射
  cli/
    app.py                  # Cyclopts 命令树与命令实现
    envelope.py             # CommandResult / 信封 / NextAction
    events.py               # stderr NDJSON 事件通道
    spec.py                 # 命令规范注册表与 schema 自省
  core/
    config.py               # 平台协议常量与并发上限
    credential_store.py     # Keyring 秘密 + SQLite 元数据
    deobfuscator.py         # 字体反混淆（动态字体结构）
    exceptions.py           # 业务异常
    exporter.py             # Markdown 原子导出（拒绝覆盖）
    homework.py             # 结构化作业提交
    models.py               # JsonValue/JsonDict、领域与结果模型、计划模型
    runner.py               # 计划编排、预算与检查点
    utils.py                # 通用小工具
    video.py                # 参数化视频引擎
  platform/
    base.py                 # 平台抽象接口与模板方法
    login_helper.py         # 临时二维码与本地回环 Web 展示
    tree_utils.py           # 课程树解析共享逻辑
    xuetangx.py             # 学堂在线适配
    yuketang.py             # 雨课堂适配

tests/
  cli/                      # 命令规范、信封、入口流程
  core/                     # 凭据、提交、视频引擎、计划、导出
  platform/                 # 平台适配

其他文件：readme.md、design.md（CLI 契约设计规格）、pyproject.toml
```

## WHERE TO CHANGE

| 需求              | 主要文件                          | 说明                         |
|-------------------|-----------------------------------|------------------------------|
| 调整视频学习策略  | `wkhelper/core/video.py`          | 心跳发送、限流重试、终态校准 |
| 调整计划编排流程  | `wkhelper/core/runner.py`         | 目标去重、参数分层、断点续学 |
| 平台 API 适配     | `wkhelper/platform/*.py`          | 仅写平台差异，不复制通用流程 |
| CLI 契约与 Schema | `wkhelper/cli/spec.py`            | 命令规范与渐进披露           |
| 输出契约与数据模型| `wkhelper/core/models.py`         | JsonValue、结果模型、计划模型|
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
  - 输出/编排层（信封、事件、Schema、命令结果）只用精确模型与 `JsonValue` / `JsonDict`
  - `Any` 仅用于平台原始载荷（HTTP JSON、章节树、字体结构）与 HTTP 附加参数等动态边界
  - CLI 命令返回 `CommandResult[...]`，不直接打印；序列化只经 `unified_result_action`
  - JSON 编码统一用 `msgspec.json.encode`（支持 dataclass 嵌套）；不要手写递归序列化
  - 需要保留缩进的文件（检查点）先用 `msgspec.to_builtins` 展开再 `json.dumps(indent=2)`
- 错误处理：
  - 库代码不要 `exit(1)`，抛出业务异常
  - 退出码：2 参数错误、3 认证缺失、4 目标不存在、5 前置条件不满足、6 平台失败、7 部分失败、1 内部错误
- 架构约束：
  - 通用流程放 `core`
  - 平台模块只写平台 API 细节
  - CLI 不得访问平台私有方法（用 `BasePlatform` 公开方法）

## ANTI-PATTERNS

- 在平台适配层复制通用逻辑（应下沉到 `core`）
- 在异步流程中调用阻塞式交互或嵌套 `asyncio.run`
- 用 `Any` / `object` 逃逸输出契约检查，或把不可序列化对象塞进输出模型
- 在 `cli/app.py` 中手工拼 JSON 或重复分页/参数校验逻辑

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

- 计划文件契约见 `design.md` §9.5 与 `core/models.py::VideoPlan`；未知字段一律报错。
- 导出目标已存在时返回 `FILE_EXISTS`（退出码 5），不覆盖既有文件。
- 全局 `--cookie-file` 仅与 `--credential-mode session` 搭配，且必须显式 `--platform`。
