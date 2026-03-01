"""UI 域综合测试：RichUI 关键交互契约。"""

import pytest

from wkhelper.ui.rich_ui import RichUI


@pytest.mark.asyncio
async def test_select_one_returns_value(monkeypatch):
    """验证单选返回选中项。"""

    class _Prompt:
        async def ask_async(self):
            return "选项A"

    monkeypatch.setattr("questionary.select", lambda *args, **kwargs: _Prompt())
    ui = RichUI()
    assert await ui.select_one("提示", ["选项A", "选项B"]) == "选项A"


@pytest.mark.asyncio
async def test_select_many_returns_values(monkeypatch):
    """验证多选返回选中项列表。"""

    class _Prompt:
        async def ask_async(self):
            return ["选项A", "选项B"]

    monkeypatch.setattr("questionary.checkbox", lambda *args, **kwargs: _Prompt())
    ui = RichUI()
    assert await ui.select_many("提示", ["选项A", "选项B"]) == ["选项A", "选项B"]


def test_eta_format():
    """验证 ETA 格式化输出。"""
    assert RichUI._format_eta(None) == "--"
    assert RichUI._format_eta(0) == "0s"
    assert RichUI._format_eta(9.4) == "9s"
    assert RichUI._format_eta(65) == "1m05s"


def test_sum_eta_seconds():
    """验证合计 ETA 计算。"""
    assert RichUI._sum_eta_seconds([]) is None
    assert RichUI._sum_eta_seconds([10.0, 20.5]) == 30.5


def test_eta_uses_video_threshold_and_rate_limit():
    """验证 ETA 会计入阈值与限流剩余时间。"""
    ui = RichUI()
    name = "视频A"
    ui._video_eta_state[name] = (0.0, 0.0, 0.02)  # 2%/s
    ui._video_rate_limit_deadline[name] = 1005.0

    # 固定当前时间，确保测试稳定
    import time

    original_monotonic = time.monotonic
    try:
        time.monotonic = lambda: 1000.0  # type: ignore[assignment]
        eta = ui._estimate_eta_seconds(name, progress=0.90)
    finally:
        time.monotonic = original_monotonic  # type: ignore[assignment]

    assert eta is not None
    # 剩余进度按 VIDEO_THRESHOLD(0.95) 计算：0.05/0.02=2.5s，按心跳(1.5s)离散后为3.0s，再加限流5s
    assert eta == 8.0
