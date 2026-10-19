"""HTTP 传输层指纹选择的契约测试。"""

import pytest

from wkhelper.core.base_platform import BasePlatform
from wkhelper.core.transport import DISABLED, resolve_impersonate
from wkhelper.platform.xuetangx import XuetangXPlatform
from wkhelper.platform.yuketang import YuketangPlatform


def test_default_impersonation_is_chrome() -> None:
    """未指定任何覆盖时使用内置浏览器目标。"""
    assert resolve_impersonate() == "chrome"
    assert BasePlatform.impersonate == "chrome"


def test_custom_user_agent_disables_impersonation() -> None:
    """自定义 UA 与浏览器指纹必然矛盾，必须关闭伪装。"""
    assert resolve_impersonate(user_agent="MyAgent/1.0") is None


def test_env_override_wins_over_platform_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量优先于平台默认值，便于临时切换或排障。"""
    monkeypatch.setenv("WKHELPER_IMPERSONATE", "safari")
    assert resolve_impersonate(None) == "safari"


def test_explicit_none_disables_impersonation() -> None:
    """`none` 是显式关闭伪装的取值。"""
    assert resolve_impersonate(DISABLED) is None
    assert resolve_impersonate(None) is None


def test_platforms_share_browser_fingerprint() -> None:
    """两个平台都默认伪装同一浏览器指纹。"""
    assert XuetangXPlatform.impersonate == "chrome"
    assert YuketangPlatform.impersonate == "chrome"
