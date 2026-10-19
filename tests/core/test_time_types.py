"""时间戳单位归一化测试。"""

from wkhelper.core.time_types import EpochMillis, epoch_millis, now_millis


def test_epoch_millis_normalizes_seconds_and_millis() -> None:
    """秒级时间戳升级为毫秒，毫秒保持不变。"""
    assert epoch_millis(1784649599) == EpochMillis(1784649599000)
    assert epoch_millis(1784649599000) == EpochMillis(1784649599000)
    assert epoch_millis("1784649599") == EpochMillis(1784649599000)


def test_epoch_millis_rejects_invalid_values() -> None:
    """空值与不可解析值统一返回 None。"""
    assert epoch_millis(None) is None
    assert epoch_millis("") is None
    assert epoch_millis(0) is None
    assert epoch_millis("not-a-time") is None
    assert epoch_millis(True) == EpochMillis(1000)


def test_now_millis_is_millisecond_scale() -> None:
    """当前时间必须是毫秒量级。"""
    assert now_millis() > 100_000_000_000
