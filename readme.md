# WKHELPER

---

![Python Version](https://img.shields.io/badge/Python-3.14%2B-blue)
![Package Manager](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)

网课平台命令行学习助手，支持雨课堂与学堂在线

## 功能

- 自动完成视频学习
- 自动完成作业(需要题库)
- 随机答题以获取答案

## 开发与测试

### 环境要求

- Python `>= 3.14`
- `uv`（依赖管理与运行）

```bash
uv sync
uv run pre-commit install
uv run ruff check . --fix
uv run ruff format .
uv run pytest -q
```

## 注意事项

> [!WARNING]
> 本工具仅用于学习与研究，请遵守学校及平台规则。使用风险与后果由使用者自行承担。
