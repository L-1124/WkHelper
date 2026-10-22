"""浏览器 CDP 交互式滑块穿透单元测试。"""

import pytest

from wkhelper.domain.errors import RiskControlError
from wkhelper.infra.browser.challenge import find_chromium_browser, solve_captcha_interactive


def test_find_chromium_browser() -> None:
    """系统存在 Chrome 或 Edge 时能够成功探测到路径。"""
    path = find_chromium_browser()
    # Windows 环境通常安装有 Edge 或 Chrome
    assert path is None or isinstance(path, str)


@pytest.mark.asyncio
async def test_solve_captcha_interactive_no_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """未安装 Chromium 浏览器时抛出 RiskControlError。"""
    monkeypatch.setattr("wkhelper.infra.browser.challenge.find_chromium_browser", lambda: None)
    with pytest.raises(RiskControlError, match="未检测到本地 Chromium 浏览器"):
        await solve_captcha_interactive("https://test.com", "get_exercise", {})
