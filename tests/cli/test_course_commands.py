"""课程命令测试（列表、详情与考核成绩看板）。"""

import json
from contextlib import asynccontextmanager
from io import StringIO
from typing import Any

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.domain.courses import Course, CourseScoreSummary, ExamScoreItem, ModuleEvaluation
from wkhelper.main import async_main


@pytest.mark.asyncio
async def test_course_inspect_includes_score_summary(fake_platform: FakeAsyncPlatform, monkeypatch: pytest.MonkeyPatch) -> None:
    """course inspect 应包含考核大纲与模块得分看板。"""
    course = Course(
        id=1001,
        name="大学心理",
        platform_id="ykt",
        metadata={"classroom_id": 2001, "university_id": 3001},
    )

    @asynccontextmanager
    async def fake_open(course_ref: str, platform: str | None = None, account: str | None = None):
        yield fake_platform, course

    async def fake_summary(platform: Any, target_course: Course) -> CourseScoreSummary:
        return CourseScoreSummary(
            modules=[
                ModuleEvaluation(name="视频", weight=0.3, total_count=10, completed_count=10, rate=1.0, score=30.0),
                ModuleEvaluation(name="作业", weight=0.2, total_count=5, completed_count=5, rate=1.0, score=20.0),
                ModuleEvaluation(name="考试", weight=0.5, total_count=1, completed_count=1, rate=0.9, score=45.0),
            ],
            exams=[
                ExamScoreItem(
                    exam_id="9001",
                    title="期末考试",
                    status="已完成",
                    user_score=90.0,
                    total_score=100.0,
                    problem_count=10,
                    done_count=10,
                )
            ],
            total_predicted_score=95.0,
        )

    monkeypatch.setattr("wkhelper.cli.commands.course._open_course", fake_open)
    monkeypatch.setattr("wkhelper.cli.commands.course.get_course_score_summary", fake_summary)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "course", "inspect", "-c", "1001:2001", "-p", "ykt"])
    assert code == 0
    data = json.loads(buf.getvalue())["data"]
    assert data["id"] == "1001:2001"
    assert data["scores"]["total_predicted_score"] == 95.0
    assert len(data["scores"]["modules"]) == 3
    assert data["scores"]["modules"][0]["name"] == "视频"
    assert data["scores"]["exams"][0]["title"] == "期末考试"


@pytest.mark.asyncio
async def test_course_inspect_markdown_table_rendering(fake_platform: FakeAsyncPlatform, monkeypatch: pytest.MonkeyPatch) -> None:
    """Markdown 渲染应生成模块表格与考试出分看板。"""
    course = Course(
        id=1001,
        name="大学心理",
        platform_id="ykt",
        metadata={"classroom_id": 2001, "university_id": 3001},
    )

    @asynccontextmanager
    async def fake_open(course_ref: str, platform: str | None = None, account: str | None = None):
        yield fake_platform, course

    async def fake_summary(platform: Any, target_course: Course) -> CourseScoreSummary:
        return CourseScoreSummary(
            modules=[
                ModuleEvaluation(name="视频", weight=0.3, total_count=10, completed_count=10, rate=1.0, score=30.0),
            ],
            exams=[
                ExamScoreItem(
                    exam_id="9001",
                    title="期末考试",
                    status="已完成",
                    user_score=90.0,
                    total_score=100.0,
                    problem_count=10,
                    done_count=10,
                )
            ],
            total_predicted_score=95.0,
        )

    monkeypatch.setattr("wkhelper.cli.commands.course._open_course", fake_open)
    monkeypatch.setattr("wkhelper.cli.commands.course.get_course_score_summary", fake_summary)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "md", "course", "inspect", "-c", "1001:2001", "-p", "ykt"])
    assert code == 0
    out = buf.getvalue()
    assert "考核大纲与完成度看板" in out
    assert "综合预估总评" in out
    assert "95.0 分" in out
