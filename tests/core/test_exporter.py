"""导出行为测试：原子写入与拒绝覆盖。"""

from pathlib import Path
from typing import Any

import pytest

from wkhelper.core.entities import Course, Homework
from wkhelper.core.exceptions import OutputExistsError
from wkhelper.core.exporter import export_questions_to_markdown, html_to_md, localize_question_detail
from wkhelper.core.results import OptionItem, QuestionDetail


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


def test_formula_multi_attribute_extraction():
    """验证从 data-latex, data-formula, data-math, alt 中提取 LaTeX 公式。"""
    md1 = html_to_md('<p><img src="f1.png" data-latex="x^2 + y^2 = 1"></p>')
    assert "$x^2 + y^2 = 1$" in md1

    md2 = html_to_md('<p><img src="f2.png" data-formula="\\frac{1}{2}" data-display="block"></p>')
    assert "$$\\frac{1}{2}$$" in md2

    md3 = html_to_md('<p><img src="f3.png" data-math="\\alpha + \\beta"></p>')
    assert "$\\alpha + \\beta$" in md3

    md4 = html_to_md('<p><img src="f4.png" alt="\\lim_{x \\to 0} f(x)"></p>')
    assert "$\\lim_{x \\to 0} f(x)$" in md4

    # 普通图片文件名不应误判为 LaTeX
    md5 = html_to_md('<p><img src="https://cdn.test/chart.png" alt="chart.png"></p>')
    assert "![chart.png](https://cdn.test/chart.png)" in md5


@pytest.mark.asyncio
async def test_export_questions_with_download_resources(fake_platform, tmp_path: Path, monkeypatch) -> None:
    """验证带 --download-resources 导出时下载图片并改写相对路径。"""
    course = Course(id="c1", name="高数", platform_id="test")

    async def fake_homeworks(current: Course) -> list[Homework]:
        return [Homework(id="hw1", name="第1讲")]

    async def fake_leaf_questions(leaf_id: str | int, current: Course) -> list[dict[str, Any]]:
        return [
            {
                "id": 101,
                "type": "single_choice",
                "content": {
                    "Body": '<p>如图所示 <img src="https://cdn.test/images/diagram.jpg"></p>',
                    "Options": [{"key": "A", "value": '<img src="https://cdn.test/images/optA.png">'}],
                },
            }
        ]

    fake_platform.get_homeworks = fake_homeworks
    fake_platform.get_leaf_questions = fake_leaf_questions

    class FakeResp:
        status_code = 200
        content = b"fake image bytes"

        def raise_for_status(self) -> None:
            return None

    async def fake_get(*args: Any, **kwargs: Any) -> FakeResp:
        return FakeResp()

    monkeypatch.setattr(fake_platform.client, "get", fake_get)

    target = tmp_path / "course.md"
    result = await export_questions_to_markdown(fake_platform, course, output_path=target, download_resources=True)

    assert result.file_path == str(target)
    assert result.resources_count == 2

    md_content = target.read_text(encoding="utf-8")
    assert "./assets/hw_hw1_q_101_body_1.jpg" in md_content
    assert "./assets/hw_hw1_q_101_opt_A_1.png" in md_content

    assets_dir = tmp_path / "assets"
    assert (assets_dir / "hw_hw1_q_101_body_1.jpg").exists()
    assert (assets_dir / "hw_hw1_q_101_opt_A_1.png").exists()
    assert (assets_dir / "hw_hw1_q_101_body_1.jpg").read_bytes() == b"fake image bytes"


@pytest.mark.asyncio
async def test_localize_question_detail(fake_platform, tmp_path: Path, monkeypatch) -> None:
    """验证单题详情资源下载与规范命名。"""
    detail = QuestionDetail(
        id="2001",
        type=None,
        prompt="题干配图：![](https://cdn.test/images/pic.png)",
        options=[OptionItem(key="A", text="选项A配图：![](https://cdn.test/images/opt.jpg)")],
        submitted=False,
        user_answer=None,
        is_correct=None,
        score=None,
        attempt_count=0,
        library_id=None,
        version=None,
    )

    class FakeResp:
        status_code = 200
        content = b"sample bytes"

        def raise_for_status(self) -> None:
            return None

    async def fake_get(*args: Any, **kwargs: Any) -> FakeResp:
        return FakeResp()

    monkeypatch.setattr(fake_platform.client, "get", fake_get)

    assets_dir = tmp_path / "assets"
    localized = await localize_question_detail(fake_platform.fetch_bytes, detail, assets_dir)

    assert len(localized.resources) == 2
    assert "./assets/q_2001_body_1.png" in localized.prompt
    assert "./assets/q_2001_opt_A_1.jpg" in localized.options[0].text
    assert (assets_dir / "q_2001_body_1.png").exists()
    assert (assets_dir / "q_2001_opt_A_1.jpg").exists()
