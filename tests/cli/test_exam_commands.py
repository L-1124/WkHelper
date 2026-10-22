"""考试命令 (exam list / inspect / start) 测试。"""

import json
from io import StringIO
from typing import Any, override

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError
from wkhelper.domain.exam import ExamCoverDetail, ExamPaperData, ExamProblemItem, ExamStartResult
from wkhelper.domain.homework import OptionItem
from wkhelper.domain.time_types import EpochMillis
from wkhelper.exam.session import ExamBrowserSession
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
    async def get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        return [{"id": 3001, "name": "期中考试", "leaf_type": 5, "start_time": 1700000000000, "end_time": 1700050000000}]

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
    async def get_exam_cached_answers(self, leaf_id: str | int, course: Course) -> dict[str, list[str]]:
        return {"9001": ["A"]}

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


async def _submitted_cover(self, leaf_id: str | int, course: Course) -> ExamCoverDetail:
    return ExamCoverDetail(
        course_id="1001",
        exam_id=str(leaf_id),
        title="期中考试测试卷",
        problem_count=20,
        total_score=100.0,
        limit_minutes=90,
        start_time=EpochMillis(1700000000000),
        deadline=EpochMillis(4102444800000),
        status="已交卷",
        description="",
        face_auth=False,
        capture_screen=False,
        raw_status=2,
    )


async def _proctored_cover(self, leaf_id: str | int, course: Course) -> ExamCoverDetail:
    return ExamCoverDetail(
        course_id="1001",
        exam_id=str(leaf_id),
        title="监考测试卷",
        problem_count=10,
        total_score=40.0,
        limit_minutes=60,
        start_time=None,
        deadline=EpochMillis(4102444800000),
        status="未开始",
        description="",
        face_auth=True,
        capture_screen=True,
        online_proctor=True,
        app_capture_screen=True,
        front_random_snap=True,
        screen_cuts=True,
        max_screen_cuts=3,
        identity_auth=True,
        max_retry=1,
    )


@pytest.fixture
def mock_exam_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "wkhelper.cli.deps._create_platform_instance",
        lambda code, client: _ExamMockPlatform(client),
    )

    async def _fake_open_browser(**kwargs: Any) -> ExamBrowserSession:
        return ExamBrowserSession(
            exam_id=str(kwargs["exam_id"]),
            platform=str(kwargs["platform"]),
            host=str(kwargs["host"]),
            eid=int(kwargs["eid"]),
            port=9222,
            profile="test-profile",
            web_url=str(kwargs["web_url"]),
        )

    monkeypatch.setattr("wkhelper.cli.commands.exam.open_exam_browser", _fake_open_browser)

    async def _fake_browser_cookies(session: ExamBrowserSession) -> dict[str, str]:
        return {}

    monkeypatch.setattr("wkhelper.cli.commands.exam.browser_cookies", _fake_browser_cookies)

    async def _fake_begin_browser_exam(session: ExamBrowserSession, timeout: float = 20.0) -> None:
        return None

    monkeypatch.setattr("wkhelper.cli.commands.exam.begin_browser_exam", _fake_begin_browser_exam)


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
async def test_exam_start_fails_when_started_false(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """当平台返回 started 为 false 时，exam start 必须报错，不能 ok true exit 0。"""

    async def _fake_start(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> ExamStartResult:
        return ExamStartResult(
            course_id="1001",
            exam_id=str(leaf_id),
            title="期中考试测试卷",
            started=False,
            limit_minutes=90,
            web_url="",
            message="考场未放行或已关闭",
        )

    monkeypatch.setattr(_ExamMockPlatform, "start_exam", _fake_start)
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(["--format", "json", "exam", "start", "-c", "1001", "-e", "3001", "--confirm", "-p", "xtzx"])
    assert code != 0
    res = json.loads(buf.getvalue().strip())
    assert res["ok"] is False
    assert "未获平台确认" in res["error"]["message"]


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

    session = ExamBrowserSession(
        exam_id="3001",
        platform="XuetangXPlatform",
        host="https://examination.test",
        eid=42,
        port=1,
        profile="p",
        web_url="https://examination.test/exam/42",
    )
    monkeypatch.setattr("wkhelper.cli.commands.exam.load_session", lambda *args: session)

    class _FakeCdp:
        events: list[dict[str, Any]] = []

        async def close(self) -> None:
            pass

    async def _fake_connect(*args: Any) -> _FakeCdp:
        return _FakeCdp()

    async def _fake_simulate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": True, "clicked_count": 1}

    async def _fake_wait(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"observed": False, "accepted": False, "answer": []}

    monkeypatch.setattr("wkhelper.cli.commands.exam.connect_exam_page", _fake_connect)
    monkeypatch.setattr("wkhelper.cli.commands.exam.simulate_browser_answer", _fake_simulate)
    monkeypatch.setattr("wkhelper.cli.commands.exam.wait_for_answer_submission", _fake_wait)
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

    buf.truncate(0)
    buf.seek(0)
    code = await async_main(
        [
            "--format",
            "json",
            "exam",
            "answer",
            "-c",
            "1001",
            "-e",
            "3001",
            "-q",
            "9001",
            "--answer",
            "A",
            "--delay",
            "0.01",
            "--delay-jitter",
            "0.005",
            "--confirm",
            "-p",
            "xtzx",
        ]
    )
    assert code == 0
    delayed = json.loads(buf.getvalue().strip())
    assert delayed["data"]["saved"] is True


@pytest.mark.asyncio
async def test_exam_answer_requires_live_browser_session(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有考场浏览器会话时拒绝作答，且不触碰页面。"""
    monkeypatch.setattr("wkhelper.cli.commands.exam.load_session", lambda *args: None)
    called: list[str] = []

    async def _fake_connect(*args: Any) -> Any:
        called.append("connect")
        raise AssertionError("不应连接未启动的考场浏览器")

    monkeypatch.setattr("wkhelper.cli.commands.exam.connect_exam_page", _fake_connect)

    code = await async_main(["exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"])
    assert code == 6
    assert called == []


def _patch_answer_driver(
    monkeypatch: pytest.MonkeyPatch,
    simulated: dict[str, Any],
    submission: dict[str, Any] | None = None,
) -> None:
    """固定浏览器会话、页面模拟结果与页面提交证据，便于验证落库判定。"""
    session = ExamBrowserSession(
        exam_id="3001",
        platform="XuetangXPlatform",
        host="https://examination.test",
        eid=42,
        port=1,
        profile="p",
        web_url="https://examination.test/exam/42",
    )
    monkeypatch.setattr("wkhelper.cli.commands.exam.load_session", lambda *args: session)

    class _FakeCdp:
        events: list[dict[str, Any]] = []

        async def close(self) -> None:
            pass

    async def _fake_connect(*args: Any) -> _FakeCdp:
        return _FakeCdp()

    async def _fake_simulate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return simulated

    async def _fake_wait(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return submission or {"observed": False, "accepted": False, "answer": []}

    monkeypatch.setattr("wkhelper.cli.commands.exam.connect_exam_page", _fake_connect)
    monkeypatch.setattr("wkhelper.cli.commands.exam.simulate_browser_answer", _fake_simulate)
    monkeypatch.setattr("wkhelper.cli.commands.exam.wait_for_answer_submission", _fake_wait)


def _observed(
    answer: list[str],
    accepted: bool = True,
    rejected: bool | None = None,
    unknown: bool | None = None,
) -> dict[str, Any]:
    """构造页面自身已发出作答请求的证据。"""
    is_rejected = not accepted if rejected is None and unknown is None else bool(rejected)
    is_unknown = bool(unknown) if unknown is not None else (not accepted and not is_rejected)
    return {
        "observed": True,
        "accepted": accepted,
        "rejected": is_rejected,
        "unknown": is_unknown,
        "answer": answer,
        "status": 200,
        "errcode": 0 if accepted else (1 if is_rejected else None),
    }


@pytest.mark.asyncio
async def test_exam_answer_reports_staged_mismatch(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """页面点击成功但服务端暂存不一致时，saved 必须为 false。"""
    _patch_answer_driver(monkeypatch, {"success": True, "clicked_count": 1})

    async def _staged_other(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> dict[str, list[str]]:
        return {"9001": ["B"]}

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cached_answers", _staged_other)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(
        ["--format", "json", "exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"]
    )
    assert code == 0
    data = json.loads(buf.getvalue())["data"]
    assert data["saved"] is False
    assert "不一致" in data["message"]


@pytest.mark.asyncio
async def test_exam_answer_reports_unverifiable_staging(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """暂存接口不可用时不得宣称已保存。"""
    from wkhelper.domain.errors import APIError as CoreAPIError

    _patch_answer_driver(monkeypatch, {"success": True, "clicked_count": 1})

    async def _staged_boom(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> dict[str, list[str]]:
        raise CoreAPIError("暂存接口不可用", retryable=True)

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cached_answers", _staged_boom)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(
        ["--format", "json", "exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"]
    )
    assert code == 0
    data = json.loads(buf.getvalue())["data"]
    assert data["saved"] is False
    assert "无法读取服务端暂存作答" in data["message"]


@pytest.mark.asyncio
async def test_exam_answer_captured_submission_skips_readback(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """捕获到页面自身的作答请求与成功响应时，不再额外回读暂存。"""
    _patch_answer_driver(monkeypatch, {"success": True, "clicked_count": 1}, submission=_observed(["A"]))

    async def _no_readback(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> dict[str, list[str]]:
        raise AssertionError("已捕获页面提交请求，不应再回读暂存作答")

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cached_answers", _no_readback)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(
        ["--format", "json", "exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"]
    )
    assert code == 0
    data = json.loads(buf.getvalue())["data"]
    assert data["saved"] is True
    assert "平台返回成功" in data["message"]


@pytest.mark.asyncio
async def test_exam_answer_contradicting_submission_is_not_saved(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """页面实际提交的内容与预期不符时直接判失败，不再用暂存回读洗白。"""
    _patch_answer_driver(monkeypatch, {"success": True, "clicked_count": 1}, submission=_observed(["B"]))

    async def _staged_matches(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> dict[str, list[str]]:
        return {"9001": ["A"]}

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cached_answers", _staged_matches)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(
        ["--format", "json", "exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"]
    )
    assert code == 0
    data = json.loads(buf.getvalue())["data"]
    assert data["saved"] is False
    assert "提交内容为" in data["message"]


@pytest.mark.asyncio
async def test_exam_answer_rejected_submission_not_washed_by_cache(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """页面作答被平台明确拒绝时，即便暂存作答匹配也不可用旧缓存洗白。"""
    _patch_answer_driver(monkeypatch, {"success": True, "clicked_count": 1}, submission=_observed(["A"], accepted=False, rejected=True))

    async def _staged_matches(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> dict[str, list[str]]:
        return {"9001": ["A"]}

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cached_answers", _staged_matches)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(
        ["--format", "json", "exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"]
    )
    assert code == 0
    data = json.loads(buf.getvalue())["data"]
    assert data["saved"] is False
    assert "平台未返回成功" in data["message"]


@pytest.mark.asyncio
async def test_exam_answer_unknown_submission_recovers_via_cache(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """页面提交状态未知（如 HTTP 200 HTML / 空 JSON）但暂存回读一致时判定保存成功。"""
    _patch_answer_driver(monkeypatch, {"success": True, "clicked_count": 1}, submission=_observed(["A"], accepted=False, unknown=True))

    async def _staged_matches(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> dict[str, list[str]]:
        return {"9001": ["A"]}

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cached_answers", _staged_matches)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(
        ["--format", "json", "exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"]
    )
    assert code == 0
    data = json.loads(buf.getvalue())["data"]
    assert data["saved"] is True
    assert "服务端暂存作答与预期一致" in data["message"]


@pytest.mark.asyncio
async def test_exam_answer_unknown_submission_fails_on_cache_mismatch(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """页面提交状态未知且暂存作答不一致时判定保存失败。"""
    _patch_answer_driver(monkeypatch, {"success": True, "clicked_count": 1}, submission=_observed(["A"], accepted=False, unknown=True))

    async def _staged_other(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> dict[str, list[str]]:
        return {"9001": ["B"]}

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cached_answers", _staged_other)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(
        ["--format", "json", "exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"]
    )
    assert code == 0
    data = json.loads(buf.getvalue())["data"]
    assert data["saved"] is False
    assert "不一致" in data["message"]


@pytest.mark.asyncio
async def test_exam_answer_unknown_submission_fails_on_cache_error(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """页面提交状态未知且无法读取暂存时判定保存失败。"""
    from wkhelper.domain.errors import APIError as CoreAPIError

    _patch_answer_driver(monkeypatch, {"success": True, "clicked_count": 1}, submission=_observed(["A"], accepted=False, unknown=True))

    async def _staged_boom(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> dict[str, list[str]]:
        raise CoreAPIError("暂存接口不可用", retryable=True)

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cached_answers", _staged_boom)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(
        ["--format", "json", "exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"]
    )
    assert code == 0
    data = json.loads(buf.getvalue())["data"]
    assert data["saved"] is False
    assert "无法读取服务端暂存作答" in data["message"]


@pytest.mark.asyncio
async def test_exam_answer_reports_simulation_failure(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """页面定位失败必须报错，不得伪称已作答。"""
    session = ExamBrowserSession(
        exam_id="3001",
        platform="XuetangXPlatform",
        host="https://examination.test",
        eid=42,
        port=1,
        profile="p",
        web_url="https://examination.test/exam/42",
    )
    monkeypatch.setattr("wkhelper.cli.commands.exam.load_session", lambda *args: session)

    class _FakeCdp:
        events: list[dict[str, Any]] = []

        async def close(self) -> None:
            pass

    async def _fake_connect(*args: Any) -> _FakeCdp:
        return _FakeCdp()

    async def _fake_simulate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": False, "error": "考场页面未找到选项: C"}

    monkeypatch.setattr("wkhelper.cli.commands.exam.connect_exam_page", _fake_connect)
    monkeypatch.setattr("wkhelper.cli.commands.exam.simulate_browser_answer", _fake_simulate)

    code = await async_main(["exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "C", "--confirm", "-p", "xtzx"])
    assert code == 6


@pytest.mark.asyncio
async def test_exam_answer_text_question_keeps_raw_answer(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """主观题不按逗号拆分，逐字送入页面输入框。"""
    session = ExamBrowserSession(
        exam_id="3001",
        platform="XuetangXPlatform",
        host="https://examination.test",
        eid=42,
        port=1,
        profile="p",
        web_url="https://examination.test/exam/42",
    )
    monkeypatch.setattr("wkhelper.cli.commands.exam.load_session", lambda *args: session)

    async def _text_paper(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> ExamPaperData:
        return ExamPaperData(
            course_id="1001",
            exam_id=str(leaf_id),
            title="期中考试测试卷",
            problems=[
                ExamProblemItem(
                    index=1,
                    id="9001",
                    type="主观题",
                    prompt="简述连续的定义。",
                    images=[],
                    options=[],
                    score=10.0,
                    answer=[],
                    has_remark=False,
                )
            ],
        )

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_paper", _text_paper)

    class _FakeCdp:
        events: list[dict[str, Any]] = []

        async def close(self) -> None:
            pass

    async def _fake_connect(*args: Any) -> _FakeCdp:
        return _FakeCdp()

    seen: dict[str, Any] = {}

    async def _fake_simulate(cdp: Any, problem_id: str, keys: list[str], mode: str = "click") -> dict[str, Any]:
        seen.update({"problem_id": problem_id, "keys": keys, "mode": mode})
        return {"success": True}

    async def _fake_wait(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _observed(["极限存在，且等于该点函数值"])

    monkeypatch.setattr("wkhelper.cli.commands.exam.connect_exam_page", _fake_connect)
    monkeypatch.setattr("wkhelper.cli.commands.exam.simulate_browser_answer", _fake_simulate)
    monkeypatch.setattr("wkhelper.cli.commands.exam.wait_for_answer_submission", _fake_wait)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(
        [
            "--format",
            "json",
            "exam",
            "answer",
            "-c",
            "1001",
            "-e",
            "3001",
            "-q",
            "9001",
            "--answer",
            "极限存在，且等于该点函数值",
            "--confirm",
            "-p",
            "xtzx",
        ]
    )
    assert code == 0
    assert seen == {"problem_id": "9001", "keys": ["极限存在，且等于该点函数值"], "mode": "text"}
    assert json.loads(buf.getvalue())["data"]["answer"] == ["极限存在，且等于该点函数值"]


@pytest.mark.asyncio
async def test_exam_answer_judge_question_maps_display_text(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """判断题界面显示“正确/错误”，作答需映射为 true/false 选项键并按暂存校验。"""

    async def _judge_paper(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> ExamPaperData:
        return ExamPaperData(
            course_id="1001",
            exam_id=str(leaf_id),
            title="期中考试测试卷",
            problems=[
                ExamProblemItem(
                    index=1,
                    id="9001",
                    type="判断题",
                    prompt="人类具有进化选择的积极天性。",
                    images=[],
                    options=[OptionItem(key="true", text="正确"), OptionItem(key="false", text="错误")],
                    score=2.0,
                    answer=["true"],
                    has_remark=False,
                )
            ],
        )

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_paper", _judge_paper)

    async def _staged_true(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> dict[str, list[str]]:
        return {"9001": ["true"]}

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cached_answers", _staged_true)
    _patch_answer_driver(monkeypatch, {"success": True, "clicked_count": 1})

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(
        ["--format", "json", "exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "正确", "--confirm", "-p", "xtzx"]
    )
    assert code == 0
    data = json.loads(buf.getvalue())["data"]
    assert data["answer"] == ["true"]
    assert data["saved"] is True


@pytest.mark.asyncio
async def test_exam_submitted_exam_cannot_reenter(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """已交卷考试不得再次入场或作答，但仍可只读查看试卷。"""
    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cover", _submitted_cover)
    opened: list[str] = []

    async def _fake_open_browser(**kwargs: Any) -> Any:
        opened.append("browser")
        raise AssertionError("已交卷考试不得拉起考场浏览器")

    monkeypatch.setattr("wkhelper.cli.commands.exam.open_exam_browser", _fake_open_browser)

    assert await async_main(["exam", "start", "-c", "1001", "-e", "3001", "--confirm", "-p", "xtzx"]) == 6
    assert await async_main(["exam", "start", "-c", "1001", "-e", "3001", "--dry-run", "-p", "xtzx"]) == 6
    assert await async_main(["exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"]) == 6
    assert opened == []

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    assert await async_main(["--format", "json", "exam", "paper", "-c", "1001", "-e", "3001", "-p", "xtzx"]) == 0
    assert json.loads(buf.getvalue())["data"]["title"] == "期中考试测试卷"


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
async def test_exam_start_blocked_when_overdue(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """已缺考的考试不允许再进入考场或读取试卷，直接返回不可重试的平台错误。"""
    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cover", _overdue_cover)

    assert await async_main(["exam", "start", "-c", "1001", "-e", "3001", "--confirm", "-p", "xtzx"]) == 6
    assert await async_main(["exam", "paper", "-c", "1001", "-e", "3001", "-p", "xtzx"]) == 6
    assert await async_main(["exam", "export", "-c", "1001", "-e", "3001", "-o", "exam.md", "-p", "xtzx"]) == 6


def test_proctoring_payload_detects_flags() -> None:
    """监考事件载荷只在任一监考项开启时生成。"""
    from wkhelper.cli.commands.exam import proctoring_payload

    quiet = ExamCoverDetail(
        course_id="1001",
        exam_id="3001",
        title="普通考试",
        problem_count=10,
        total_score=40.0,
        limit_minutes=0,
        start_time=None,
        deadline=None,
        status="未开始",
        description="",
        face_auth=False,
        capture_screen=False,
    )
    assert proctoring_payload(quiet) is None

    app_only = ExamCoverDetail(
        course_id="5812766",
        exam_id="84705331",
        title="期末考试",
        problem_count=28,
        total_score=51.0,
        limit_minutes=0,
        start_time=None,
        deadline=None,
        status="未开始",
        description="",
        face_auth=False,
        capture_screen=False,
        app_capture_screen=True,
        front_random_snap=True,
    )
    assert proctoring_payload(app_only) is None

    proctored = ExamCoverDetail(
        course_id="1001",
        exam_id="3001",
        title="监考考试",
        problem_count=10,
        total_score=40.0,
        limit_minutes=60,
        start_time=None,
        deadline=None,
        status="未开始",
        description="",
        face_auth=True,
        capture_screen=True,
        online_proctor=True,
        app_capture_screen=True,
        front_random_snap=True,
        screen_cuts=True,
        max_screen_cuts=3,
        identity_auth=True,
        max_retry=1,
    )
    payload = proctoring_payload(proctored)
    assert payload is not None
    assert payload["exam_id"] == "3001"
    assert payload["capture_screen"] is True
    assert payload["max_retry"] == 1


@pytest.mark.asyncio
async def test_exam_start_dry_run_emits_proctoring_event(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """考试写操作前必须输出监考事件。"""
    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cover", _proctored_cover)
    out, err = StringIO(), StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)

    code = await async_main(["--format", "json", "exam", "start", "-c", "1001", "-e", "3001", "--dry-run", "-p", "xtzx"])
    assert code == 0
    events = [json.loads(line) for line in err.getvalue().strip().splitlines() if line.strip()]
    assert any(event["event"] == "security.exam_proctoring" for event in events)


@pytest.mark.asyncio
async def test_exam_start_and_answer_blocked_by_proctoring(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """强监考考试只允许预览，进入考场与作答都在触达页面前被拦下。"""
    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cover", _proctored_cover)
    session = ExamBrowserSession(
        exam_id="3001",
        platform="XuetangXPlatform",
        host="https://examination.test",
        eid=42,
        port=1,
        profile="p",
        web_url="https://examination.test/exam/42",
    )
    monkeypatch.setattr("wkhelper.cli.commands.exam.load_session", lambda *args: session)

    async def _fail_connect(*args: Any) -> Any:
        raise AssertionError("强监考考试不得连接考场页面")

    monkeypatch.setattr("wkhelper.cli.commands.exam.connect_exam_page", _fail_connect)

    assert await async_main(["exam", "start", "-c", "1001", "-e", "3001", "--confirm", "-p", "xtzx"]) == 6
    assert await async_main(["exam", "answer", "-c", "1001", "-e", "3001", "-q", "9001", "--answer", "A", "--confirm", "-p", "xtzx"]) == 6
    assert await async_main(["exam", "status", "-c", "1001", "-e", "3001", "-p", "xtzx"]) == 6
    assert await async_main(["exam", "result", "-c", "1001", "-e", "3001", "-p", "xtzx"]) == 6


def test_exam_session_roundtrip(tmp_path) -> None:
    """会话文件可持久化、读取与清理。"""
    from wkhelper.exam.session import ExamBrowserSession, clear_session, load_session, save_session

    session = ExamBrowserSession(
        exam_id="3001",
        platform="XuetangXPlatform",
        host="https://examination.test",
        eid=42,
        port=1,
        profile=str(tmp_path / "profile"),
        web_url="https://examination.test/exam/42",
    )
    save_session(session, tmp_path)
    assert load_session(tmp_path, verify=False) == session
    clear_session(session, tmp_path)
    assert load_session(tmp_path, verify=False) is None


@pytest.mark.asyncio
async def test_exam_close_without_session(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """没有活跃会话时 exam close 返回未关闭而不是报错。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "--data-dir", str(tmp_path), "exam", "close"])
    assert code == 0

    res = json.loads(buf.getvalue())
    assert res["data"]["closed"] is False


@pytest.mark.asyncio
async def test_exam_start_begin_creates_attempt(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--begin` 才真正开始作答，普通 --confirm 只建立考场会话。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "exam", "start", "-c", "1001", "-e", "3001", "--confirm", "-p", "xtzx"])
    assert code == 0
    plain = json.loads(buf.getvalue())["data"]
    assert plain["started"] is True

    buf.truncate(0)
    buf.seek(0)
    code = await async_main(["--format", "json", "exam", "start", "-c", "1001", "-e", "3001", "--confirm", "--begin", "-p", "xtzx"])
    assert code == 0
    begun = json.loads(buf.getvalue())["data"]
    assert begun["started"] is True


@pytest.mark.asyncio
async def test_exam_start_without_begin_skips_browser_begin(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """未传 --begin 时只建立考场会话，不得调用 begin_browser_exam。"""
    browser_begun = False

    async def _spy_begin_browser(session: ExamBrowserSession, timeout: float = 20.0) -> None:
        nonlocal browser_begun
        browser_begun = True

    monkeypatch.setattr("wkhelper.cli.commands.exam.begin_browser_exam", _spy_begin_browser)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "exam", "start", "-c", "1001", "-e", "3001", "--confirm", "-p", "xtzx"])
    assert code == 0
    data = json.loads(buf.getvalue())["data"]
    assert data["started"] is True
    assert "考场会话已成功建立" in data["message"]
    assert not browser_begun


@pytest.mark.asyncio
async def test_exam_start_dry_run_never_calls_browser_begin_and_preserves_guards(
    mock_exam_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dry-run 传入 --begin 不得调用 browser begin，同时保留已缺考、已交卷与强监考门禁。"""
    begin_called = False

    async def _fail_begin(session: ExamBrowserSession, timeout: float = 20.0) -> None:
        nonlocal begin_called
        begin_called = True
        raise AssertionError("dry-run 不得调用 begin_browser_exam")

    monkeypatch.setattr("wkhelper.cli.commands.exam.begin_browser_exam", _fail_begin)

    out, err = StringIO(), StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)

    # 1. 正常封面 dry-run --begin：返回预览数据，绝不调用 begin_browser_exam
    code = await async_main(["--format", "json", "exam", "start", "-c", "1001", "-e", "3001", "--dry-run", "--begin", "-p", "xtzx"])
    assert code == 0
    res = json.loads(out.getvalue())
    assert res["ok"] is True
    assert res["data"]["dry_run"] is True
    assert not begin_called

    # 2. 已交卷门禁保留：dry-run --begin 遇到已交卷直接报 6，绝不调用 begin
    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cover", _submitted_cover)
    out.truncate(0)
    out.seek(0)
    code = await async_main(["--format", "json", "exam", "start", "-c", "1001", "-e", "3001", "--dry-run", "--begin", "-p", "xtzx"])
    assert code == 6
    res = json.loads(out.getvalue())
    assert res["ok"] is False
    assert "已交卷" in res["error"]["message"]
    assert not begin_called

    # 3. 强监考门禁保留：dry-run --begin 遇到强监考照常输出监考警告事件
    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cover", _proctored_cover)
    out.truncate(0)
    out.seek(0)
    err.truncate(0)
    err.seek(0)
    code = await async_main(["--format", "json", "exam", "start", "-c", "1001", "-e", "3001", "--dry-run", "--begin", "-p", "xtzx"])
    assert code == 0
    events = [json.loads(line) for line in err.getvalue().strip().splitlines() if line.strip()]
    assert any(event["event"] == "security.exam_proctoring" for event in events)
    assert not begin_called


@pytest.mark.asyncio
async def test_exam_start_begin_browser_failure_exits(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """begin_browser_exam 失败时应正确透传平台错误，退出码 6，非 mock 回声。"""

    async def _failing_begin(session: ExamBrowserSession, timeout: float = 20.0) -> None:
        raise APIError("网页开始答题响应无法确认，请在浏览器核对；不要重复开始", retryable=False)

    monkeypatch.setattr("wkhelper.cli.commands.exam.begin_browser_exam", _failing_begin)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "exam", "start", "-c", "1001", "-e", "3001", "--confirm", "--begin", "-p", "xtzx"])
    assert code == 6
    res = json.loads(buf.getvalue())
    assert res["ok"] is False
    assert res["error"]["code"] == "PLATFORM_ERROR"
    assert "网页开始答题响应无法确认" in res["error"]["message"]
    assert res["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_exam_result_reports_not_submitted_when_unstarted(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """未交卷时 10107 表示尚未提交，不能误报进度异常。"""

    async def fake_result(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> dict[str, Any]:
        return {"errcode": 10107, "errmsg": "试卷尚未提交", "problem_results": []}

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_result", fake_result)
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "exam", "result", "-c", "1001", "-e", "3001", "-p", "xtzx"])
    assert code == 0
    message = json.loads(buf.getvalue())["data"]["message"]
    assert "答卷尚未提交" in message
    assert "试卷进度异常" not in message


@pytest.mark.asyncio
async def test_exam_result_reports_progress_anomaly_when_submitted(mock_exam_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """封面已交卷但 10107 表示试卷进度异常，需联系老师。"""
    orig_cover = _ExamMockPlatform.get_exam_cover

    async def fake_cover(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> ExamCoverDetail:
        base = await orig_cover(self, leaf_id, course)
        return ExamCoverDetail.model_validate(dict(base.model_dump(exclude={"has_limit"}), status="已交卷", raw_status=2))

    async def fake_result(self: _ExamMockPlatform, leaf_id: str | int, course: Course) -> dict[str, Any]:
        return {"errcode": 10107, "errmsg": "试卷尚未提交", "problem_results": []}

    monkeypatch.setattr(_ExamMockPlatform, "get_exam_cover", fake_cover)
    monkeypatch.setattr(_ExamMockPlatform, "get_exam_result", fake_result)
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "exam", "result", "-c", "1001", "-e", "3001", "-p", "xtzx"])
    assert code == 0
    message = json.loads(buf.getvalue())["data"]["message"]
    assert "试卷已交卷但进度异常" in message
    assert "老师" in message
