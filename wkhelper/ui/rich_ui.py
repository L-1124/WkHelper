"""基于 Rich 的终端交互实现。"""

import logging
import math
import re
import time
from threading import Lock
from typing import Any, ClassVar

import questionary
from questionary import Choice, Style
from rich.box import HEAVY
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from wkhelper.core.config import HEARTBEAT_INTERVAL, LEARNING_RATE, VIDEO_THRESHOLD


class RichUI:
    """基于 rich 的美化终端交互。"""

    _QUESTIONARY_STYLE = Style(
        [
            ("qmark", "fg:#5fafff bold"),
            ("question", "bold"),
            ("answer", "fg:#af5f00 bold"),
            ("pointer", "fg:#5fafff bold"),
            ("highlighted", "fg:#5fafff bold"),
            ("selected", "fg:#cc5454"),
            ("separator", "fg:#cc5454"),
            ("instruction", "fg:#808080"),
        ]
    )

    def __init__(self):
        self.console = Console()
        self._video_progress: dict[str, float] = {}
        self._video_status: dict[str, str] = {}
        self._video_eta_state: dict[str, tuple[float, float, float | None]] = {}
        self._video_rate_limit_deadline: dict[str, float] = {}
        self._video_progress_live: Live | None = None
        self._video_progress_lock = Lock()
        self._homework_progress: dict[str, tuple[int, int]] = {}
        self._homework_status: dict[str, str] = {}
        self._homework_progress_live: Live | None = None
        self._homework_progress_lock = Lock()
        self._setup_logging()

    def _setup_logging(self):
        """配置并安装标准 logging 处理器。"""
        logger = logging.getLogger("wkhelper")
        logger.setLevel(logging.INFO)
        # 避免重复添加 Handler
        if not logger.handlers:
            logger.addHandler(self.StandardRichHandler(self))

    class StandardRichHandler(logging.Handler):
        """将标准 logging 适配到 RichUI 的处理器。"""

        _STYLE_MAP: ClassVar[dict[int, str]] = {
            logging.DEBUG: "blue",
            logging.INFO: "blue",
            logging.WARNING: "yellow",
            logging.ERROR: "bold red",
            logging.CRITICAL: "bold red",
        }

        def __init__(self, ui: RichUI):
            super().__init__()
            self.ui = ui

        def emit(self, record: logging.LogRecord):
            try:
                style = self._STYLE_MAP.get(record.levelno, "white")
                msg = self.format(record)
                self.ui.console.print(f"[{style}]{msg}[/{style}]")
            except Exception:
                self.handleError(record)

    async def select_one(self, message: str, choices: list[str]) -> str | None:
        """单项选择。"""
        self.console.print(f"[bold blue]{message}[/bold blue]")
        return await questionary.select(
            "请选择",
            choices=choices,
            style=self._QUESTIONARY_STYLE,
            instruction="（↑/↓ 选择，Enter 确认）",
        ).ask_async()

    async def select_many(
        self,
        message: str,
        choices: list[str],
        default_selected: set[str] | None = None,
        disabled_choices: set[str] | None = None,
    ) -> list[str] | None:
        """多项多选。"""
        default_selected = default_selected or set()
        disabled_choices = disabled_choices or set()
        q_choices = [
            Choice(
                title=item,
                checked=(item in default_selected and item not in disabled_choices),
                disabled="已完成" if item in disabled_choices else None,
            )
            for item in choices
        ]
        self.console.print(f"[bold blue]{message}[/bold blue]")
        return await questionary.checkbox(
            "请选择",
            choices=q_choices,
            style=self._QUESTIONARY_STYLE,
            instruction="（↑/↓ 移动，Space 勾选，Enter 确认）",
        ).ask_async()

    async def confirm(self, message: str, default: bool = True) -> bool:
        """确认操作。"""
        return bool(
            await questionary.confirm(
                message,
                default=default,
                style=self._QUESTIONARY_STYLE,
            ).ask_async()
        )

    def show_table(self, title: str, columns: list[str], rows: list[list[str]]) -> None:
        """显示数据表格。"""
        table = Table(title=title, header_style="bold magenta")
        for col in columns:
            table.add_column(col)
        for row in rows:
            table.add_row(*row)
        self.console.print(table)

    def track_progress(self, title: str, total: int) -> Any:
        """跟踪批任务执行进度。"""
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
        )
        task_id = progress.add_task(title, total=total)
        progress.start()

        class Tracker:
            def update(self, advance: int = 1):
                progress.update(task_id, advance=advance)

            def stop(self):
                progress.stop()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                self.stop()

        return Tracker()

    def _render_video_progress_panel(self) -> Panel:
        table = Table(
            show_header=True,
            header_style="bold cyan",
            box=None,
            expand=True,
            show_lines=False,
            pad_edge=False,
        )
        table.add_column("视频任务")
        table.add_column("进度", justify="right")
        table.add_column("预计剩余", justify="right")
        table.add_column("状态")
        known_etas: list[float] = []
        for name, progress in sorted(
            self._video_progress.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            status = self._video_status.get(name, "观看中")
            eta_seconds = self._estimate_eta_seconds(name, progress)
            if eta_seconds is not None:
                known_etas.append(eta_seconds)
            table.add_row(name, f"{progress * 100:.1f}%", self._format_eta(eta_seconds), status)
        total_eta = self._sum_eta_seconds(known_etas)
        subtitle = f"总预计剩余: {self._format_eta(total_eta)}"
        return Panel(
            table,
            border_style="blue",
            title=None,
            subtitle=subtitle,
            subtitle_align="right",
            expand=True,
            box=HEAVY,
        )

    def _estimate_eta_seconds(self, video_name: str, progress: float) -> float | None:
        """估算剩余秒数：理论剩余 + 限流剩余。"""
        state = self._video_eta_state.get(video_name)
        speed: float | None = None
        if state:
            _, _, speed = state

        remain_progress = max(0.0, VIDEO_THRESHOLD - progress)
        if remain_progress <= 0:
            return 0.0

        # 使用配置兜底：默认 8 倍速时约 1%/s，按 LEARNING_RATE 线性缩放
        fallback_speed = 0.01 * (LEARNING_RATE / 8.0)
        eff_speed = speed if (speed is not None and speed > 0) else fallback_speed
        if eff_speed <= 0:
            return None

        raw_eta = remain_progress / eff_speed
        # 按心跳粒度离散化（每 HEARTBEAT_INTERVAL 才会推进一轮）
        ticks = max(1, math.ceil(raw_eta / HEARTBEAT_INTERVAL))
        base_eta = ticks * HEARTBEAT_INTERVAL
        return base_eta + self._get_rate_limit_remaining(video_name)

    @staticmethod
    def _format_eta(eta_seconds: float | None) -> str:
        """格式化剩余时间文本。"""
        if eta_seconds is None:
            return "--"
        if eta_seconds <= 0:
            return "0s"
        total = int(round(eta_seconds))
        minutes, seconds = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h{minutes:02d}m{seconds:02d}s"
        if minutes > 0:
            return f"{minutes}m{seconds:02d}s"
        return f"{seconds}s"

    @staticmethod
    def _sum_eta_seconds(etas: list[float]) -> float | None:
        """聚合所有视频 ETA（合计剩余时长）。"""
        if not etas:
            return None
        return sum(etas)

    def _get_rate_limit_remaining(self, video_name: str) -> float:
        """获取当前视频剩余限流等待时间（秒）。"""
        deadline = self._video_rate_limit_deadline.get(video_name)
        if deadline is None:
            return 0.0
        return max(0.0, deadline - time.monotonic())

    def update_video_progress(self, video_name: str, progress: float) -> None:
        """更新视频进度面板。"""
        with self._video_progress_lock:
            now = time.monotonic()
            prev = self._video_eta_state.get(video_name)
            if prev is None:
                self._video_eta_state[video_name] = (progress, now, None)
            else:
                prev_progress, prev_ts, prev_speed = prev
                dt = now - prev_ts
                dp = progress - prev_progress
                speed = prev_speed
                if dt > 0 and dp > 0:
                    instant_speed = dp / dt
                    speed = instant_speed if prev_speed is None else (prev_speed * 0.7 + instant_speed * 0.3)
                self._video_eta_state[video_name] = (progress, now, speed)
            self._video_progress[video_name] = progress
            if self._video_progress_live is None:
                self._video_progress_live = Live(
                    self._render_video_progress_panel(),
                    console=self.console,
                    refresh_per_second=6,
                    transient=True,
                )
                self._video_progress_live.start()
            else:
                self._video_progress_live.update(self._render_video_progress_panel())

    def finish_video_progress(self, video_name: str) -> None:
        """结束单个视频进度展示。"""
        with self._video_progress_lock:
            self._video_progress.pop(video_name, None)
            self._video_status.pop(video_name, None)
            self._video_eta_state.pop(video_name, None)
            self._video_rate_limit_deadline.pop(video_name, None)
            if self._video_progress_live is None:
                return
            if self._video_progress:
                self._video_progress_live.update(self._render_video_progress_panel())
            else:
                self._video_progress_live.stop()
                self._video_progress_live = None

    def update_video_status(self, video_name: str, status: str | None) -> None:
        """更新视频状态展示；None/空字符串表示清空状态。"""
        with self._video_progress_lock:
            if status:
                self._video_status[video_name] = status
                # 兼容状态文案：⚠️ 限流，等待 40s
                match = re.search(r"限流，等待\s+(\d+)s", status)
                if match:
                    self._video_rate_limit_deadline[video_name] = time.monotonic() + float(match.group(1))
            else:
                self._video_status.pop(video_name, None)
                self._video_rate_limit_deadline.pop(video_name, None)
            if self._video_progress_live is not None:
                self._video_progress_live.update(self._render_video_progress_panel())

    def _render_homework_progress_panel(self) -> Panel:
        table = Table(
            show_header=True,
            header_style="bold cyan",
            box=None,
            expand=True,
            show_lines=False,
            pad_edge=False,
        )
        table.add_column("作业任务")
        table.add_column("进度", justify="right")
        table.add_column("状态")

        for name, (done, total) in sorted(
            self._homework_progress.items(),
            key=lambda item: (item[1][1] and (item[1][0] / item[1][1]), item[1][0]),
            reverse=True,
        ):
            percent = 0.0 if total <= 0 else done / total
            status = self._homework_status.get(name, "答题中")
            table.add_row(name, f"{done}/{total} ({percent * 100:.1f}%)", status)

        return Panel(table, border_style="green", title=None, expand=True, box=HEAVY)

    def update_homework_progress(self, homework_name: str, done: int, total: int) -> None:
        """更新作业题目进度面板。"""
        with self._homework_progress_lock:
            self._homework_progress[homework_name] = (done, total)
            if self._homework_progress_live is None:
                self._homework_progress_live = Live(
                    self._render_homework_progress_panel(),
                    console=self.console,
                    refresh_per_second=6,
                    transient=True,
                )
                self._homework_progress_live.start()
            else:
                self._homework_progress_live.update(self._render_homework_progress_panel())

    def finish_homework_progress(self, homework_name: str) -> None:
        """结束单个作业进度展示。"""
        with self._homework_progress_lock:
            self._homework_progress.pop(homework_name, None)
            self._homework_status.pop(homework_name, None)
            if self._homework_progress_live is None:
                return
            if self._homework_progress:
                self._homework_progress_live.update(self._render_homework_progress_panel())
            else:
                self._homework_progress_live.stop()
                self._homework_progress_live = None

    def update_homework_status(self, homework_name: str, status: str | None) -> None:
        """更新作业状态展示；None/空字符串表示清空状态。"""
        with self._homework_progress_lock:
            if status:
                self._homework_status[homework_name] = status
            else:
                self._homework_status.pop(homework_name, None)
            if self._homework_progress_live is not None:
                self._homework_progress_live.update(self._render_homework_progress_panel())
