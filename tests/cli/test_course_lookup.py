"""课程引用解析测试：课堂级引用优先，原生 ID 歧义时拒绝猜测。"""

import pytest

from wkhelper.cli.app import _load_course
from wkhelper.core.models import Course


def _courses() -> list[Course]:
    return [
        Course(id=1, name="主班", platform_id="test", metadata={"classroom_id": 10}),
        Course(id=1, name="子班", platform_id="test", metadata={"classroom_id": 11}),
    ]


@pytest.mark.asyncio
async def test_load_course_prefers_explicit_classroom_reference(fake_platform) -> None:
    """完整引用能精确命中指定课堂。"""

    async def fake_courses() -> list[Course]:
        return _courses()

    fake_platform.get_courses = fake_courses  # type: ignore[method-assign]

    assert (await _load_course(fake_platform, "1:11")).name == "子班"
    assert (await _load_course(fake_platform, "1:10")).name == "主班"


@pytest.mark.asyncio
async def test_load_course_rejects_ambiguous_native_id(fake_platform) -> None:
    """同一课程挂在多个课堂时，原生 ID 不再被静默解析到某一个课堂。"""

    async def fake_courses() -> list[Course]:
        return _courses()

    fake_platform.get_courses = fake_courses  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="多个课堂"):
        await _load_course(fake_platform, "1")


@pytest.mark.asyncio
async def test_load_course_accepts_unique_native_id(fake_platform) -> None:
    """只有一个课堂时仍允许直接用原生课程 ID。"""

    async def fake_courses() -> list[Course]:
        return [Course(id=1, name="唯一课堂", platform_id="test", metadata={"classroom_id": 10})]

    fake_platform.get_courses = fake_courses  # type: ignore[method-assign]

    assert (await _load_course(fake_platform, "1")).name == "唯一课堂"


@pytest.mark.asyncio
async def test_load_course_reports_not_found(fake_platform) -> None:
    """不存在时抛 KeyError，由入口映射为 NOT_FOUND。"""

    async def fake_courses() -> list[Course]:
        return _courses()

    fake_platform.get_courses = fake_courses  # type: ignore[method-assign]

    with pytest.raises(KeyError):
        await _load_course(fake_platform, "999")
