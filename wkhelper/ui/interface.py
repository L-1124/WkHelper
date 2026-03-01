"""UI 接口定义。"""

from typing import Any, Protocol


class UserInterface(Protocol):
    """用户交互协议。"""

    async def select_one(self, message: str, choices: list[str]) -> str | None:
        """单项选择。"""
        ...

    async def select_many(
        self,
        message: str,
        choices: list[str],
        default_selected: set[str] | None = None,
        disabled_choices: set[str] | None = None,
    ) -> list[str] | None:
        """多项选择。"""
        ...

    async def confirm(self, message: str, default: bool = True) -> bool:
        """确认操作。"""
        ...

    def show_table(self, title: str, columns: list[str], rows: list[list[str]]) -> None:
        """显示表格。"""
        ...

    def track_progress(self, title: str, total: int) -> Any:
        """跟踪批任务执行进度。"""
        ...

    def update_video_progress(self, video_name: str, progress: float) -> None:
        """更新视频进度显示。"""
        ...

    def finish_video_progress(self, video_name: str) -> None:
        """结束单个视频进度显示。"""
        ...

    def update_video_status(self, video_name: str, status: str | None) -> None:
        """更新视频状态文本（如限流/重试）。"""
        ...

    def update_homework_progress(self, homework_name: str, done: int, total: int) -> None:
        """更新作业题目进度显示。"""
        ...

    def finish_homework_progress(self, homework_name: str) -> None:
        """结束单个作业进度显示。"""
        ...

    def update_homework_status(self, homework_name: str, status: str | None) -> None:
        """更新作业状态文本。"""
        ...
