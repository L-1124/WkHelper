"""UI 接口定义。"""

from typing import Any, Protocol

type TableRows = list[list[str]]


class UserInterface(Protocol):
    """用户交互协议。"""

    async def input_text(self, message: str, default: str = "") -> str | None:
        """文本输入。"""
        ...

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

    def show_table(self, title: str, columns: list[str], rows: TableRows) -> None:
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

    def print_message(self, message: str) -> None:
        """打印消息。"""
        ...

    def stop_live_displays(self) -> None:
        """暂停所有动态进度显示（如进度条），以便交互式对话框正常渲染。"""
        ...

    def start_live_displays(self) -> None:
        """恢复之前暂停的动态进度显示。"""
        ...
