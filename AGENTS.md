# PROJECT KNOWLEDGE BASE

**Updated:** 2026-03-01  
**Branch:** main

## OVERVIEW

`wkhelper` 是一个网课自动化工具，支持雨课堂与学堂在线。

- 平台：`yuketang.cn` / `xuetangx.com`
- 架构：异步（`asyncio` + `httpx` + `TaskGroup`）
- 交互：`rich`（展示）+ `questionary`（选择）

核心思路是把通用流程放在 `wkhelper/core`，平台差异放在 `wkhelper/platform`。

## STRUCTURE

```text
./
├── readme.md
├── pyproject.toml
├── tests/
│   ├── core/
│   ├── platform/
│   └── ui/
└── wkhelper/
    ├── main.py                 # 程序入口（wkhelper 脚本）
    ├── core/
    │   ├── config.py           # 配置（速率/心跳/并发）
    │   ├── db.py               # SQLite 题库
    │   ├── exceptions.py       # 业务异常
    │   ├── homework.py         # 作业通用逻辑
    │   ├── models.py           # 数据模型
    │   ├── runner.py           # 菜单/批处理调度
    │   └── video.py            # 视频通用心跳逻辑
    ├── platform/
    │   ├── base.py             # 平台抽象接口
    │   ├── tree_utils.py       # 课程树解析共享逻辑
    │   ├── yuketang.py         # 雨课堂适配
    │   └── xuetangx.py         # 学堂在线适配
    └── ui/
        ├── interface.py        # UI 协议
        └── rich_ui.py          # Rich + Questionary 实现
```

## WHERE TO CHANGE

| 需求              | 主要文件                          | 说明                         |
|-------------------|-----------------------------------|------------------------------|
| 调整视频学习策略  | `wkhelper/core/video.py`          | 心跳发送、限流重试、进度回调 |
| 调整批处理流程    | `wkhelper/core/runner.py`         | 视频/作业/下载入口与并发     |
| 平台 API 适配     | `wkhelper/platform/*.py`          | 仅写平台差异，不复制通用流程 |
| 课程树显示/筛选   | `wkhelper/platform/tree_utils.py` | 两平台共用                   |
| 终端交互/进度面板 | `wkhelper/ui/rich_ui.py`          | 选择器、表格、ETA            |
| 配置项修改        | `wkhelper/core/config.py`         | 速率、心跳、阈值、并发       |

## CONVENTIONS

- **必须使用 `uv`**：
  - `uv sync`
  - `uv run wkhelper`
  - `uv run pytest -q`
- 代码风格：
  - 注释与 docstring 使用中文
  - 统一现代类型标注（`list[str]`、`dict[str, Any]`）
- 错误处理：
  - 库代码不要 `exit(1)`，抛出业务异常
- 架构约束：
  - 通用流程放 `core`
  - 平台模块只写平台 API 细节

## ANTI-PATTERNS

- 在平台适配层复制通用逻辑（应下沉到 `core`）
- 在异步流程中调用阻塞式交互或嵌套 `asyncio.run`

## COMMANDS

```bash
# 运行
uv run wkhelper

# 质量检查
uv run ruff check . --fix
uv run ruff format .

# 测试
uv run pytest -q
```

## NOTES

- 当前已有 pytest 测试（`tests/`）。
- 本地题库默认文件：`wkhelper/questions.db`。
