"""考试命令 (exam list / inspect / start) 测试。"""

import json
from io import StringIO
from typing import override

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.core.entities import Course
from wkhelper.core.results import (
    ExamAnswerResult,
    ExamCoverDetail,
    ExamItem,
    ExamPaperData,
    ExamProblemItem,
    ExamStartResult,
    ExamSubmitResult,
    OptionItem,
)
from wkhelper.core.time_types import EpochMillis
from wkhelper.main import async_main


class _ExamMockPlatform(FakeAsyncPlatform):
    """带考试接口假实现的平台。"""

    @override
    async def get_courses(self) -> list[Course]:
        return [
            Course(
                id="1001",
                name="高等数学",
                platform_id="test",
                metadata={"classroom_id": "2001", "sign": "sign-123"},
            )
        ]

    @override
    async def get_exams(self, course: Course) -> list[ExamItem]:
        return [
            ExamItem(
                id="3001",
                name="期中考试",
                start_time=EpochMillis(1700000000000),
                deadline=EpochMillis(1700050000000),
                completed=False,
            )
        ]

    @override
    async def get_exam_cover(self, leaf_id: str | int, course: Course) -> ExamCoverDetail:
        return ExamCoverDetail(
            course_id="1001",
            exam_id=str(leaf_id),
            title="期中考试测试卷",
            problem_count=20,
            total_score=100.0,
            limit_minutes=90,
            start_time=EpochMillis(1700000000000),
            deadline=EpochMillis(1700050000000),
            status="未开始",
            description="请在规定时间内完成作答",
            face_auth=False,
            capture_screen=False,
        )

    @override
    async def get_exam_answer_state(self, leaf_id: str | int, course: Course) -> tuple[list[str], list[str]]:
        return ["9001"], ["9001", "9002"]

    @override
    async def answer_exam_problem(self, leaf_id: str | int, course: Course, problem_id: str, answer: list[str]) -> ExamAnswerResult:
        return ExamAnswerResult(
            dry_run=False,
            exam_id=str(leaf_id),
            problem_id=problem_id,
            answer=answer,
            saved=True,
            message="作答已保存",
        )

    @override
    async def submit_exam(self, leaf_id: str | int, course: Course) -> ExamSubmitResult:
        return ExamSubmitResult(
            dry_run=False,
            exam_id=str(leaf_id),
            answered_count=1,
            unanswered=["9002"],
            submitted=True,
            message="交卷成功",
        )

    @override
    async def get_exam_paper(self, leaf_id: str | int, course: Course) -> ExamPaperData:
        return ExamPaperData(
            course_id="1001",
            exam_id=str(leaf_id),
            title="期中考试测试卷",
            problems=[
                ExamProblemItem(
                    index=1,
                    id="9001",
                    type="单选题",
                    prompt="函数在 x=0 处连续吗？",
                    images=[],
                    options=[OptionItem(key="A", text="连续"), OptionItem(key="B", text="不连续")],
                    score=4.0,
                    answer=["A"],
                    has_remark=False,
                )
            ],
        )

    @override
    async def start_exam(self, leaf_id: str | int, course: Course) -> ExamStartResult:
        return ExamStartResult(
            course_id="1001",
            exam_id=str(leaf_id),
            title="期中考试测试卷",
            started=True,
            limit_minutes=90,
            web_url="https://examination.test/exam/3001",
            message="考场会话已成功建立",
        )


async def _overdue_cover(self, leaf_id: str | int, course: Course) -> ExamCoverDetail:
    return ExamCoverDetail(
        course_id="1001",
        exam_id=str(leaf_id),
        title="期中考试测试卷",
        problem_count=20,
        total_score=100.0,
        limit_minutes=90,
        start_time=EpochMillis(1700000000000),
        deadline=EpochMillis(1700050000000),
        status="已缺考",
        description="",
        face_auth=False,
        capture_screen=False,
    )


@pytest.fixture
def mock_exam_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "wkhelper.cli.deps._create_platform_instance",
        lambda code, client: _ExamMockPlatform(client),
    )


@pytest.mark.asyncio
async def test_exam_list(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 exam list 输出考试列表数据。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "exam", "list", "-c", "1001", "-p", "xtzx"])
    assert code == 0
    res = json.loads(buf.getvalue().strip())
    assert res["ok"] is True
    exams = res["data"]["exams"]
    assert len(exams) == 1
    assert exams[0]["id"] == "3001"
    assert exams[0]["name"] == "期中考试"
    assert exams[0]["overdue"] is True


@pytest.mark.asyncio
async def test_exam_inspect(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 exam inspect 获取考试封面与元数据，并给出开考指引。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "exam", "inspect", "-c", "1001", "-e", "3001", "-p", "xtzx"])
    assert code == 0
    res = json.loads(buf.getvalue().strip())
    assert res["ok"] is True
    detail = res["data"]
    assert detail["title"] == "期中考试测试卷"
    assert detail["problem_count"] == 20
    assert detail["limit_minutes"] == 90
    assert detail["total_score"] == 100.0
    actions = res.get("next_actions", [])
    assert len(actions) == 2
    assert "--dry-run" in " ".join(actions[0]["argv"])
    assert "--confirm" in " ".join(actions[1]["argv"])


@pytest.mark.asyncio
async def test_exam_start_requires_confirm(mock_exam_env: None) -> None:
    """未传 --confirm 或 --dry-run 时触发门禁。"""
    code = await async_main(["exam", "start", "-c", "1001", "-e", "3001", "-p", "xtzx"])
    assert code == 5


@pytest.mark.asyncio
async def test_exam_start_dry_run(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 exam start --dry-run 安全预览。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "exam", "start", "-c", "1001", "-e", "3001", "--dry-run", "-p", "xtzx"])
    assert code == 0
    res = json.loads(buf.getvalue().strip())
    assert res["ok"] is True
    assert res["data"]["dry_run"] is True
    assert res["data"]["limit_minutes"] == 90


@pytest.mark.asyncio
async def test_exam_start_confirm(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 exam start --confirm 正式进入考场。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "exam", "start", "-c", "1001", "-e", "3001", "--confirm", "-p", "xtzx"])
    assert code == 0
    res = json.loads(buf.getvalue().strip())
    assert res["ok"] is True
    assert res["data"]["started"] is True
    assert "https://examination.test" in res["data"]["web_url"]


@pytest.mark.asyncio
async def test_exam_paper(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 exam paper 输出考场试卷题目。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "exam", "paper", "-c", "1001", "-e", "3001", "-p", "xtzx"])
    assert code == 0
    res = json.loads(buf.getvalue().strip())
    assert res["ok"] is True
    assert res["data"]["title"] == "期中考试测试卷"
    problems = res["data"]["problems"]
    assert len(problems) == 1
    assert problems[0]["id"] == "9001"
    assert problems[0]["answer"] == ["A"]


@pytest.mark.asyncio
async def test_exam_export_writes_markdown(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """exam export 写出 Markdown，拒绝覆盖已有文件。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    target = tmp_path / "exam.md"

    code = await async_main(
        ["--format", "json", "exam", "export", "-c", "1001", "-e", "3001", "-o", str(target), "--no-download-images", "-p", "xtzx"]
    )
    assert code == 0
    body = target.read_text(encoding="utf-8")
    assert "## 第 1 题" in body
    assert "**平台答案：** A" in body
    res = json.loads(buf.getvalue().strip())
    assert res["data"]["problems_count"] == 1

    code = await async_main(["--format", "json", "exam", "export", "-c", "1001", "-e", "3001", "-o", str(target), "-p", "xtzx"])
    assert code == 5


@pytest.mark.asyncio
async def test_exam_answer_requires_confirm(mock_exam_env: None) -> None:
    """exam answer 未传门禁参数时拒绝执行。"""
    code = await async_main(["exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "-p", "xtzx"])
    assert code == 5


@pytest.mark.asyncio
async def test_exam_answer_dry_run_and_confirm(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """exam answer dry-run 不写入；confirm 写入并返回保存状态。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(
        ["--format", "json", "exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--dry-run", "-p", "xtzx"]
    )
    assert code == 0
    preview = json.loads(buf.getvalue().strip())
    assert preview["data"]["dry_run"] is True
    assert preview["data"]["saved"] is False

    buf.truncate(0)
    buf.seek(0)
    code = await async_main(
        ["--format", "json", "exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"]
    )
    assert code == 0
    saved = json.loads(buf.getvalue().strip())
    assert saved["data"]["saved"] is True
    assert saved["data"]["answer"] == ["A"]


@pytest.mark.asyncio
async def test_exam_submit_gate_dry_run_and_confirm(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """exam submit 门禁、预览未答题、确认交卷三条路径。"""
    code = await async_main(["exam", "submit", "-c", "1001", "-e", "3001", "-p", "xtzx"])
    assert code == 5

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(["--format", "json", "exam", "submit", "-c", "1001", "-e", "3001", "--dry-run", "-p", "xtzx"])
    assert code == 0
    preview = json.loads(buf.getvalue().strip())
    assert preview["data"]["answered_count"] == 1
    assert preview["data"]["unanswered"] == ["9002"]
    assert preview["data"]["submitted"] is False

    buf.truncate(0)
    buf.seek(0)
    code = await async_main(["--format", "json", "exam", "submit", "-c", "1001", "-e", "3001", "--confirm", "-p", "xtzx"])
    assert code == 0
    result = json.loads(buf.getvalue().strip())
    assert result["data"]["submitted"] is True


@pytest.mark.asyncio
async def test_exam_inspect_overdue(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """已缺考的考试不再给出开考 action，而是明确说明无法进入。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    monkeypatch.setattr(
        _ExamMockPlatform,
        "get_exam_cover",
        _overdue_cover,
    )

    code = await async_main(["--format", "json", "exam", "inspect", "-c", "1001", "-e", "3001", "-p", "xtzx"])
    assert code == 0
    res = json.loads(buf.getvalue().strip())
    assert res["data"]["status"] == "已缺考"
    actions = res["next_actions"]
    assert len(actions) == 1
    assert actions[0]["argv"] == []
    assert "已缺考" in actions[0]["reason"]


@pytest.mark.asyncio
async def test_exam_start_and_submit_blocked_when_overdue(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """已缺考的考试不允许再进入考场或交卷，直接返回不可重试的平台错误。"""
    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cover", _overdue_cover)

    assert await async_main(["exam", "start", "-c", "1001", "-e", "3001", "--confirm", "-p", "xtzx"]) == 6
    assert await async_main(["exam", "submit", "-c", "1001", "-e", "3001", "--confirm", "-p", "xtzx"]) == 6
    assert await async_main(["exam", "submit", "-c", "1001", "-e", "3001", "--dry-run", "-p", "xtzx"]) == 6
