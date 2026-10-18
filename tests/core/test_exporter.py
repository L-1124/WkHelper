"""导出行为测试：原子写入与拒绝覆盖。"""

from pathlib import Path
from typing import Any

import pytest

from wkhelper.core.exceptions import OutputExistsError
from wkhelper.core.exporter import export_questions_to_markdown
from wkhelper.core.models import Course, Homework


@pytest.mark.asyncio
async def test_export_writes_markdown_and_refuses_overwrite(fake_platform, tmp_path: Path) -> None:
    course = Course(id="c1", name="测试课程", platform_id="test")

    async def fake_homeworks(current: Course) -> list[Homework]:
        return [Homework(id="hw1", name="作业一")]

    async def fake_leaf_questions(leaf_id: str | int, current: Course) -> list[dict[str, Any]]:
        return [
            {
                "problem_id": 1,
                "type": "single_choice",
                "content": {"Body": "<p>题干</p>", "Options": [{"key": "A", "value": "选项A"}]},
            }
        ]

    fake_platform.get_homeworks = fake_homeworks  # type: ignore[method-assign]
    fake_platform.get_leaf_questions = fake_leaf_questions  # type: ignore[method-assign]

    target = tmp_path / "course.md"
    result = await export_questions_to_markdown(fake_platform, course, output_path=target)

    assert result.file_path == str(target)
    assert result.homeworks_count == 1
    assert result.questions_count == 1
    assert "题干" in target.read_text(encoding="utf-8")

    # 第二次导出必须拒绝覆盖既有文件
    with pytest.raises(OutputExistsError):
        await export_questions_to_markdown(fake_platform, course, output_path=target)


@pytest.mark.asyncio
async def test_export_returns_empty_result_without_homeworks(fake_platform, tmp_path: Path) -> None:
    course = Course(id="c1", name="空课程", platform_id="test")

    async def fake_homeworks(current: Course) -> list[Homework]:
        return []

    fake_platform.get_homeworks = fake_homeworks  # type: ignore[method-assign]

    result = await export_questions_to_markdown(fake_platform, course, output_path=tmp_path / "empty.md")

    assert result.file_path is None
    assert result.homeworks_count == 0
    assert not (tmp_path / "empty.md").exists()
