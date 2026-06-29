"""基于 Rich 的终端交互实现。"""

import asyncio
import logging
import math
import re
import time
from threading import Lock
from typing import Any, ClassVar

import questionary
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style as PTStyle
from questionary import Style
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
from wkhelper.ui.interface import TableRows


class ProgressGroup:
    """通用的进度追踪组（用于视频、作业等面板的底层复用）。"""

    def __init__(self, on_update=None):
        self.progress: dict[str, Any] = {}
        self.status: dict[str, str] = {}
        self.eta_state: dict[str, tuple[Any, float, float | None]] = {}
        self.rate_limit_deadline: dict[str, float] = {}
        self.lock = Lock()
        self.on_update = on_update

    def update_progress(self, name: str, progress_val: Any, progress_data: Any) -> None:
        """
        更新进度。
        progress_val: 用于计算速度的值（如视频的 progress，作业的 done）
        progress_data: 用于显示的值（如视频的 progress，作业的 (done, total)）
        """
        with self.lock:
            now = time.monotonic()
            prev = self.eta_state.get(name)
            if prev is None:
                self.eta_state[name] = (progress_val, now, None)
            else:
                prev_val, prev_ts, prev_speed = prev
                dt = now - prev_ts
                dp = progress_val - prev_val
                speed = prev_speed
                if dt > 0 and dp > 0:
                    instant_speed = dp / dt
                    speed = instant_speed if prev_speed is None else (prev_speed * 0.7 + instant_speed * 0.3)
                self.eta_state[name] = (progress_val, now, speed)

            self.progress[name] = progress_data
            if self.on_update:
                self.on_update()

    def update_status(self, name: str, status: str | None, default_progress_val: Any, default_progress_data: Any) -> None:
        with self.lock:
            if name not in self.progress:
                now = time.monotonic()
                self.progress[name] = default_progress_data
                self.eta_state[name] = (default_progress_val, now, None)

            if status:
                self.status[name] = status
                match = re.search(r"限流，等待\s+(\d+)s", status)
                if match:
                    self.rate_limit_deadline[name] = time.monotonic() + float(match.group(1))
            else:
                self.status.pop(name, None)
                self.rate_limit_deadline.pop(name, None)

            if self.on_update:
                self.on_update()

    def finish(self, name: str) -> None:
        with self.lock:
            self.progress.pop(name, None)
            self.status.pop(name, None)
            self.eta_state.pop(name, None)
            self.rate_limit_deadline.pop(name, None)
            if self.on_update:
                self.on_update()


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
        self._video_group = ProgressGroup(on_update=self._refresh_global_live)
        self._homework_group = ProgressGroup(on_update=self._refresh_global_live)
        self._tracker_progress = None
        self._global_live = None
        self._global_lock = Lock()
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

        def __init__(self, ui: "RichUI"):
            super().__init__()
            self.ui = ui

        def emit(self, record: logging.LogRecord):
            try:
                style = self._STYLE_MAP.get(record.levelno, "white")
                msg = self.format(record)
                self.ui.console.print(f"[{style}]{msg}[/{style}]")
            except Exception:
                self.handleError(record)

    def _get_global_renderable(self):
        from rich.console import Group

        renderables = []
        if self._tracker_progress is not None:
            renderables.append(self._tracker_progress)
        if self._video_group.progress:
            renderables.append(self._render_video_progress_panel())
        if self._homework_group.progress:
            renderables.append(self._render_homework_progress_panel())

        if not renderables:
            return None
        return Group(*renderables)

    def _refresh_global_live(self):
        with self._global_lock:
            renderable = self._get_global_renderable()
            if renderable is None:
                if self._global_live is not None:
                    self._global_live.stop()
                    self._global_live = None
            else:
                if self._global_live is None:
                    self._global_live = Live(
                        renderable,
                        console=self.console,
                        refresh_per_second=6,
                        transient=True,
                    )
                    self._global_live.start()
                else:
                    self._global_live.update(renderable)

    def stop_live_displays(self) -> None:
        """暂停所有动态进度显示。"""
        with self._global_lock:
            if self._global_live is not None:
                self._global_live.stop()

    def start_live_displays(self) -> None:
        """恢复动态进度显示。"""
        with self._global_lock:
            if self._global_live is not None:
                self._global_live.start()

    def print_message(self, message: str) -> None:
        """打印消息。"""
        self.console.print(message)

    async def input_text(self, message: str, default: str = "") -> str | None:
        """文本输入。"""
        self.console.print(f"[bold blue]{message}[/bold blue]")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, input, "> ")
        return result.strip() or default

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
        """多项多选（支持 Ctrl+A 全选）。"""
        default_selected = default_selected or set()
        disabled = disabled_choices or set()
        if not choices:
            self.console.print("[yellow]没有可选择的项目[/yellow]")
            return []

        selectable_choices = [item for item in choices if item not in disabled]
        if not selectable_choices:
            self.console.print("[yellow]所有项目均已完成，无需选择[/yellow]")
            return []

        return await self._pt_checkbox(message, choices, default_selected, disabled)

    async def _pt_checkbox(
        self,
        message: str,
        choices: list[str],
        default_selected: set[str],
        disabled: set[str],
    ) -> list[str] | None:
        """基于 prompt_toolkit 的多选组件，支持 Ctrl+A 全选。"""
        all_checked: set[str] = set(default_selected)
        current = 0
        count = len(choices)

        kb = KeyBindings()

        @kb.add("up")
        def _(event):
            nonlocal current
            current = (current - 1) % count

        @kb.add("down")
        def _(event):
            nonlocal current
            current = (current + 1) % count

        @kb.add("space")
        def _(event):
            item = choices[current]
            if item not in disabled:
                if item in all_checked:
                    all_checked.discard(item)
                else:
                    all_checked.add(item)

        @kb.add("c-a")
        def _(event):
            for item in choices:
                if item not in disabled:
                    all_checked.add(item)

        @kb.add("enter")
        def _(event):
            event.app.exit(result=[c for c in choices if c in all_checked])

        @kb.add("c-c")
        @kb.add("c-d")
        def _(event):
            event.app.exit(result=None)

        def _render():
            lines: list[tuple[str, str]] = [("class:message", message + "\n")]
            for i, item in enumerate(choices):
                pointer = "›" if i == current else " "
                is_current = i == current

                if item in disabled:
                    mark = "✗"
                    style_class = "class:disabled"
                elif item in all_checked:
                    mark = "✓"
                    style_class = "class:current" if is_current else "class:chosen"
                else:
                    mark = "○"
                    style_class = "class:current" if is_current else ""

                lines.append((style_class, f" {pointer}  {mark}  {item}\n"))

            lines.append(("class:instruction", "\n ↑/↓ 移动  Space 勾选  Ctrl+A 全选  Enter 确认"))
            return lines

        control = FormattedTextControl(text=_render, show_cursor=False)
        layout = Layout(HSplit([Window(control)]))

        style = PTStyle.from_dict(
            {
                "message": "bold",
                "current": "bold fg:#5fafff",
                "chosen": "fg:#5fafff",
                "disabled": "fg:#666666",
                "instruction": "fg:#808080",
            }
        )

        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            erase_when_done=True,
        )

        return await app.run_async()

    async def confirm(self, message: str, default: bool = True) -> bool:
        """确认操作。"""
        return bool(
            await questionary.confirm(
                message,
                default=default,
                style=self._QUESTIONARY_STYLE,
            ).ask_async()
        )

    def show_table(self, title: str, columns: list[str], rows: TableRows) -> None:
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
            self._video_group.progress.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            status = self._video_group.status.get(name, "观看中")
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
        state = self._video_group.eta_state.get(video_name)
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
        return base_eta + self._get_video_rate_limit_remaining(video_name)

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

    def _get_video_rate_limit_remaining(self, video_name: str) -> float:
        """获取当前视频剩余限流等待时间（秒）。"""
        deadline = self._video_group.rate_limit_deadline.get(video_name)
        if deadline is None:
            return 0.0
        return max(0.0, deadline - time.monotonic())

    def update_video_progress(self, video_name: str, progress: float) -> None:
        """更新视频进度面板。"""
        self._video_group.update_progress(video_name, progress, progress)

    def finish_video_progress(self, video_name: str) -> None:
        """结束单个视频进度展示。"""
        self._video_group.finish(video_name)

    def update_video_status(self, video_name: str, status: str | None) -> None:
        """更新视频状态展示；None/空字符串表示清空状态。"""
        self._video_group.update_status(video_name, status, 0.0, 0.0)

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
        table.add_column("预计剩余", justify="right")
        table.add_column("状态")

        known_etas: list[float] = []
        for name, (done, total) in sorted(
            self._homework_group.progress.items(),
            key=lambda item: (item[1][1] and (item[1][0] / item[1][1]), item[1][0]),
            reverse=True,
        ):
            percent = 0.0 if total <= 0 else done / total
            status = self._homework_group.status.get(name, "答题中")
            eta_seconds = self._estimate_homework_eta_seconds(name, done, total)
            if eta_seconds is not None:
                known_etas.append(eta_seconds)
            table.add_row(name, f"{done}/{total} ({percent * 100:.1f}%)", self._format_eta(eta_seconds), status)

        total_eta = self._sum_eta_seconds(known_etas)
        subtitle = f"总预计剩余: {self._format_eta(total_eta)}"
        return Panel(
            table,
            border_style="green",
            title=None,
            subtitle=subtitle,
            subtitle_align="right",
            expand=True,
            box=HEAVY,
        )

    def _estimate_homework_eta_seconds(self, homework_name: str, done: int, total: int) -> float | None:
        """估算作业剩余秒数：理论剩余 + 限流剩余。"""
        if total <= 0 or done >= total:
            return 0.0 if total > 0 else None

        state = self._homework_group.eta_state.get(homework_name)
        speed: float | None = None
        if state:
            _, _, speed = state

        # 作业冷启动兜底：默认每题约 3 秒
        fallback_speed = 1.0 / 3.0
        eff_speed = speed if (speed is not None and speed > 0) else fallback_speed
        if eff_speed <= 0:
            return None

        remain_count = max(0, total - done)
        base_eta = remain_count / eff_speed
        return base_eta + self._get_homework_rate_limit_remaining(homework_name)

    def _get_homework_rate_limit_remaining(self, homework_name: str) -> float:
        """获取当前作业剩余限流等待时间（秒）。"""
        deadline = self._homework_group.rate_limit_deadline.get(homework_name)
        if deadline is None:
            return 0.0
        return max(0.0, deadline - time.monotonic())

    def update_homework_progress(self, homework_name: str, done: int, total: int) -> None:
        """更新作业题目进度面板。"""
        self._homework_group.update_progress(homework_name, done, (done, total))

    def finish_homework_progress(self, homework_name: str) -> None:
        """结束单个作业进度展示。"""
        self._homework_group.finish(homework_name)

    def update_homework_status(self, homework_name: str, status: str | None) -> None:
        """更新作业状态展示；None/空字符串表示清空状态。"""
        self._homework_group.update_status(homework_name, status, 0, (0, 0))
