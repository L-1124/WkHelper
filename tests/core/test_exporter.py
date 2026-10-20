"""导出行为测试：原子写入与拒绝覆盖。"""

import asyncio
import errno
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from wkhelper.core.entities import Course, Homework
from wkhelper.core.exceptions import APIError, OutputExistsError
from wkhelper.core.exporter import (
    _publish_file_no_clobber,
    export_exam_paper_to_markdown,
    export_questions_to_markdown,
    html_to_md,
    localize_question_detail,
)
from wkhelper.core.results import ExamPaperData, ExamProblemItem, OptionItem, QuestionDetail


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
async def test_export_reports_each_homework_failure(fake_platform, tmp_path: Path) -> None:
    """全部作业抓取失败时必须保留节点与原因，不能退化成笼统内部错误。"""
    course = Course(id="c1", name="测试课程", platform_id="test")

    async def fake_homeworks(current: Course) -> list[Homework]:
        return [Homework(id="hw1", name="第一章作业"), Homework(id="hw2", name="第二章作业")]

    async def fake_leaf_questions(leaf_id: str | int, current: Course) -> list[dict[str, Any]]:
        raise APIError(f"节点 {leaf_id} 是考试（leaf_type=5）", retryable=False)

    fake_platform.get_homeworks = fake_homeworks  # type: ignore[method-assign]
    fake_platform.get_leaf_questions = fake_leaf_questions  # type: ignore[method-assign]

    target = tmp_path / "failed.md"
    with pytest.raises(APIError) as excinfo:
        await export_questions_to_markdown(fake_platform, course, output_path=target)

    message = str(excinfo.value)
    assert "hw1" in message
    assert "hw2" in message
    assert "第一章作业" in message
    assert "leaf_type=5" in message
    assert not target.exists()


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


@pytest.mark.asyncio
async def test_export_duplicate_homework_names(fake_platform, tmp_path: Path) -> None:
    """同名作业应由唯一作业 ID 索引，不得互相覆盖、丢题或产生错误统计计数。"""
    course = Course(id="c1", name="测试同名课程", platform_id="test")

    async def fake_homeworks(current: Course) -> list[Homework]:
        return [
            Homework(id="hw_101", name="第一讲随堂测验"),
            Homework(id="hw_102", name="第一讲随堂测验"),
        ]

    async def fake_leaf_questions(leaf_id: str | int, current: Course) -> list[dict[str, Any]]:
        if str(leaf_id) == "hw_101":
            return [
                {
                    "problem_id": 1,
                    "type": "single_choice",
                    "content": {"Body": "<p>第一讲测验第一题</p>", "Options": [{"key": "A", "value": "A1"}]},
                },
                {
                    "problem_id": 2,
                    "type": "single_choice",
                    "content": {"Body": "<p>第一讲测验第二题</p>", "Options": [{"key": "B", "value": "B1"}]},
                },
            ]
        return [
            {
                "problem_id": 3,
                "type": "single_choice",
                "content": {"Body": "<p>第二讲同名测验专属题目</p>", "Options": [{"key": "C", "value": "C1"}]},
            }
        ]

    fake_platform.get_homeworks = fake_homeworks  # type: ignore[method-assign]
    fake_platform.get_leaf_questions = fake_leaf_questions  # type: ignore[method-assign]

    target = tmp_path / "duplicate_names.md"
    result = await export_questions_to_markdown(fake_platform, course, output_path=target)

    assert result.file_path == str(target)
    assert result.homeworks_count == 2
    assert result.questions_count == 3

    content = target.read_text(encoding="utf-8")
    assert "第一讲测验第一题" in content
    assert "第一讲测验第二题" in content
    assert "第二讲同名测验专属题目" in content
    assert list(tmp_path.glob("wk_export_*.md")) == []


@pytest.mark.asyncio
async def test_export_partial_failure_raises_api_error_and_does_not_publish(fake_platform, tmp_path: Path) -> None:
    """任一作业抓取失败不得普通成功或半份导出，必须统一携带节点与原因抛出 APIError 且不发布文件。"""
    course = Course(id="c1", name="部分失败课程", platform_id="test")

    async def fake_homeworks(current: Course) -> list[Homework]:
        return [
            Homework(id="hw_ok", name="正常作业"),
            Homework(id="hw_bad", name="异常作业"),
        ]

    async def fake_leaf_questions(leaf_id: str | int, current: Course) -> list[dict[str, Any]]:
        if str(leaf_id) == "hw_ok":
            return [
                {
                    "problem_id": 1,
                    "content": {"Body": "<p>正常作业题干</p>"},
                }
            ]
        raise RuntimeError("平台网关超时 504")

    fake_platform.get_homeworks = fake_homeworks  # type: ignore[method-assign]
    fake_platform.get_leaf_questions = fake_leaf_questions  # type: ignore[method-assign]

    target = tmp_path / "partial_fail.md"
    with pytest.raises(APIError) as excinfo:
        await export_questions_to_markdown(fake_platform, course, output_path=target)

    err_msg = str(excinfo.value)
    assert "异常作业" in err_msg
    assert "hw_bad" in err_msg
    assert "平台网关超时 504" in err_msg
    assert not target.exists()
    assert list(tmp_path.glob("wk_export_*.md")) == []


@pytest.mark.asyncio
async def test_export_publish_target_race_collision(fake_platform, tmp_path: Path) -> None:
    """导出过程中若目标文件被竞态新建，发布时必须拒绝覆盖并清理临时文件。"""
    course = Course(id="c1", name="竞态测试课程", platform_id="test")
    target = tmp_path / "race_target.md"

    async def fake_homeworks(current: Course) -> list[Homework]:
        return [Homework(id="hw1", name="作业")]

    async def fake_leaf_questions(leaf_id: str | int, current: Course) -> list[dict[str, Any]]:
        # 模拟在抓取题目期间，外部进程竞态创建了目标文件
        target.write_text("外部已有关键文件内容", encoding="utf-8")
        return [{"problem_id": 1, "content": {"Body": "<p>抓取到的题目</p>"}}]

    fake_platform.get_homeworks = fake_homeworks  # type: ignore[method-assign]
    fake_platform.get_leaf_questions = fake_leaf_questions  # type: ignore[method-assign]

    with pytest.raises(OutputExistsError) as excinfo:
        await export_questions_to_markdown(fake_platform, course, output_path=target)

    assert "输出文件已存在，拒绝覆盖" in str(excinfo.value)
    assert target.read_text(encoding="utf-8") == "外部已有关键文件内容"
    assert list(tmp_path.glob("wk_export_*.md")) == []


@pytest.mark.asyncio
async def test_export_concurrent_double_export(fake_platform, tmp_path: Path) -> None:
    """并发双导出同一路径时，仅一份能成功发布，另一份必须报 OutputExistsError，文件无损坏且无临时文件残留。"""
    course = Course(id="c1", name="并发双导出课程", platform_id="test")
    target = tmp_path / "double_export.md"

    async def fake_homeworks(current: Course) -> list[Homework]:
        return [Homework(id="hw1", name="作业一")]

    async def fake_leaf_questions(leaf_id: str | int, current: Course) -> list[dict[str, Any]]:
        await asyncio.sleep(0.01)
        return [{"problem_id": 1, "content": {"Body": "<p>并发题目内容</p>"}}]

    fake_platform.get_homeworks = fake_homeworks  # type: ignore[method-assign]
    fake_platform.get_leaf_questions = fake_leaf_questions  # type: ignore[method-assign]

    results = await asyncio.gather(
        export_questions_to_markdown(fake_platform, course, output_path=target),
        export_questions_to_markdown(fake_platform, course, output_path=target),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], OutputExistsError)
    assert target.exists()
    assert "并发题目内容" in target.read_text(encoding="utf-8")
    assert list(tmp_path.glob("wk_export_*.md")) == []


@pytest.mark.asyncio
async def test_export_exam_paper_publish_race_and_no_clobber(fake_platform, tmp_path: Path) -> None:
    """考场试卷导出时竞态存在目标文件应拒绝覆盖，并清理临时文件。"""
    course = Course(id="c1", name="考场课程", platform_id="test")
    target = tmp_path / "exam_target.md"

    async def fake_get_exam_paper(exam_id: str | int, current: Course) -> ExamPaperData:
        target.write_text("预存试卷内容，严禁覆盖", encoding="utf-8")
        return ExamPaperData(
            course_id="c1",
            exam_id=str(exam_id),
            title="期末大考",
            problems=[
                ExamProblemItem(
                    id="p101",
                    index=1,
                    type="单选题",
                    score=10.0,
                    prompt="期末第一题",
                    images=[],
                    options=[OptionItem(key="A", text="选项A")],
                    answer=["A"],
                    has_remark=False,
                )
            ],
        )

    fake_platform.get_exam_paper = fake_get_exam_paper  # type: ignore[method-assign]

    with pytest.raises(OutputExistsError):
        await export_exam_paper_to_markdown(fake_platform, course, "exam_999", output_path=target, download_resources=False)

    assert target.read_text(encoding="utf-8") == "预存试卷内容，严禁覆盖"
    assert list(tmp_path.glob("wk_exam_*.md")) == []


def test_publish_non_file_exists_os_error_raised_directly(tmp_path: Path) -> None:
    """非 FileExistsError 的系统错误必须原样真实抛出，绝不降级为 copy/replace 覆盖已有文件。"""
    src = tmp_path / "temp_file.md"
    src.write_text("临时写入", encoding="utf-8")
    dst = tmp_path / "dest_file.md"

    with patch("os.link", side_effect=OSError(errno.EXDEV, "Invalid cross-device link")):
        with pytest.raises(OSError) as excinfo:
            _publish_file_no_clobber(str(src), dst)

    assert not isinstance(excinfo.value, OutputExistsError)
    assert excinfo.value.errno == errno.EXDEV
    assert not dst.exists()
