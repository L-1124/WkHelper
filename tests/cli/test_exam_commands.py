"""考试命令 (exam list / inspect / start) 测试。"""

import json
from io import StringIO
from typing import override

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.core.entities import Course
from wkhelper.core.results import ExamCoverDetail, ExamItem, ExamStartResult
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
                start_time=1700000000000,
                deadline=1700050000000,
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
            start_time=1700000000000,
            deadline=1700050000000,
            status="未开始",
            description="请在规定时间内完成作答",
            face_auth=False,
            capture_screen=False,
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


@pytest.mark.asyncio
async def test_exam_inspect(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 exam inspect 获取考试封面与元数据。"""
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


@pytest.mark.asyncio
async def test_exam_start_requires_confirm(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
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
