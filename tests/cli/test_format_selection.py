"""输出格式选择与 TTY 智能探测测试。"""

import json
from io import StringIO

import pytest

from wkhelper.cli.markdown import format_error_markdown, format_result_markdown
from wkhelper.core.entities import AccountSummary, StorageStatus
from wkhelper.core.plan import ExecutionSettings, VideoTarget
from wkhelper.core.results import (
    AccountListData,
    ArticleItem,
    ArticleListData,
    ArticleReadResult,
    AuthStatusResult,
    CourseDetail,
    CourseItem,
    CourseListData,
    DiscussionCommentResult,
    DiscussionItem,
    DiscussionListData,
    DiscussionTopicDetail,
    ExportResult,
    HomeworkItem,
    HomeworkListData,
    HomeworkOutline,
    HomeworkSubmitPreview,
    HomeworkSubmitResult,
    LoginResult,
    OptionItem,
    OutputFileResult,
    QuestionDetail,
    QuestionListData,
    QuestionOutline,
    QuestionSubmitPreview,
    QuestionSubmitResult,
    QuestionSummary,
    RemoveResult,
    StatusData,
    SubmitItem,
    VideoItem,
    VideoLearnPreview,
    VideoListData,
    VideoPlanSummary,
    VideoProgress,
    VideoTargetResult,
)
from wkhelper.main import async_main


@pytest.mark.asyncio
async def test_format_explicit_md(monkeypatch):
    """显式指定 --format md 时输出 Markdown 格式。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "md", "--credential-mode", "session", "status"])
    assert code == 0
    text = buf.getvalue().strip()
    assert text.startswith("## 本地运行环境与账号概况")
    assert "凭据保管模式" in text


@pytest.mark.asyncio
async def test_format_explicit_json(monkeypatch):
    """显式指定 --format json 时输出标准单行 JSON 信封。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "json", "--credential-mode", "session", "status"])
    assert code == 0
    text = buf.getvalue().strip()
    parsed = json.loads(text)
    assert parsed["ok"] is True
    assert parsed["schema_version"] == 1


@pytest.mark.asyncio
async def test_format_non_tty_defaults_to_json(monkeypatch):
    """非 TTY 环境（如管道/重定向/测试）缺省时自动默认为 json。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--credential-mode", "session", "status"])
    assert code == 0
    text = buf.getvalue().strip()
    # 能够被 json 解析即代表默认输出了 json 信封
    parsed = json.loads(text)
    assert parsed["ok"] is True


@pytest.mark.asyncio
async def test_format_tty_defaults_to_md(monkeypatch):
    buf = StringIO()
    monkeypatch.setattr(buf, "isatty", lambda: True)
    monkeypatch.setattr("sys.stdout", buf)
    code = await async_main(["--credential-mode", "session", "status"])
    assert code == 0
    text = buf.getvalue().strip()
    assert "## 本地运行环境与账号概况" in text or "凭据保管模式" in text


@pytest.mark.asyncio
async def test_format_error_renders_markdown_when_requested(monkeypatch):
    """请求 md 格式时异常输出为 Markdown 报错横幅。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["--format", "md", "--credential-mode", "session", "course", "inspect", "-c", "invalid_id"])
    assert code != 0
    text = buf.getvalue().strip()
    assert "## 操作失败" in text


def _storage_status() -> StorageStatus:
    return StorageStatus(
        mode="system",
        backend="WinVaultKeyring",
        available=True,
        priority=5,
        data_dir="/tmp/wkhelper",
        metadata_db="/tmp/wkhelper/metadata.db",
    )


def _account() -> AccountSummary:
    return AccountSummary(platform="ykt", user_id="u1", name="张三", school="重庆大学", saved_at=0.0)


def _result_samples() -> list[object]:
    """所有已登记渲染函数的领域结果模型最小实例。

    新增结果模型时必须同步登记渲染函数并把样例加到这里，否则覆盖用例会漏测。
    """
    return [
        AccountListData(accounts=[_account()]),
        ArticleListData(articles=[ArticleItem(id="a1", name="导学材料", completed=True)]),
        ArticleReadResult(course_id="c1", leaf_id="a1", name="导学材料", success=True, completed=True),
        AuthStatusResult(platform="ykt", user_id="u1", name="张三", valid=True),
        CourseDetail(id="c1", name="测试课程", platform="ykt", metadata={"章节数": 3}),
        CourseListData(courses=[CourseItem(id="c1", name="测试课程", platform="ykt", classroom_id=123)]),
        DiscussionCommentResult(course_id="c1", leaf_id="d1", topic_id=1, comment_id=2, success=True, message="ok"),
        DiscussionListData(discussions=[DiscussionItem(id="d1", name="互动交流", completed=False)]),
        DiscussionTopicDetail(
            course_id="c1",
            leaf_id="d1",
            topic_id=1,
            title="讨论题",
            prompt="题干",
            comment_count=10,
            user_comment_num=0,
            completed=False,
        ),
        ExportResult(file_path="out.md", homeworks_count=1, questions_count=2, resources_count=1),
        HomeworkListData(homeworks=[HomeworkItem(id="h1", name="第1次作业", completed=False)]),
        HomeworkOutline(
            course_id="c1",
            homework_id="h1",
            total_questions=1,
            questions_summary=[QuestionOutline(index=1, id="q1", type="single", library_id=None)],
        ),
        HomeworkSubmitPreview(dry_run=True, course_id="c1", homework_id="h1", answers_count=1, validated=True),
        HomeworkSubmitResult(
            total=1,
            submitted_count=1,
            correct_count=1,
            items=[SubmitItem(index=1, question_id="q1", submitted=True, is_correct=True, correct_answer=["A"])],
        ),
        LoginResult(platform="ykt", user_id="u1", name="张三", school="重庆大学", persisted=True),
        OutputFileResult(output_file="out.json", written=True),
        QuestionDetail(
            id="q1",
            type="single",
            prompt="题干",
            options=[OptionItem(key="A", text="选项 A")],
            submitted=False,
            user_answer=None,
            is_correct=None,
            score=None,
            attempt_count=0,
            library_id=None,
            version=None,
        ),
        QuestionListData(
            questions=[
                QuestionSummary(
                    index=1,
                    id="q1",
                    type="single",
                    prompt="题干",
                    content_truncated=False,
                    options=[OptionItem(key="A", text="选项 A")],
                    submitted=False,
                    is_correct=None,
                )
            ]
        ),
        QuestionSubmitPreview(dry_run=True, course_id="c1", homework_id="h1", question_id="q1", answer=["A"]),
        QuestionSubmitResult(question_id="q1", submitted_answer=["A"], success=True, is_correct=True, correct_answer=["A"]),
        RemoveResult(platform="ykt", account="u1", deleted=True),
        StatusData(credential_storage=_storage_status(), accounts_count=1, accounts=[_account()]),
        _storage_status(),
        VideoLearnPreview(
            dry_run=True,
            targets_count=1,
            targets=[VideoTarget(course_id="c1", video_id="v1")],
            requested_config=ExecutionSettings(),
            effective_config=ExecutionSettings(),
            note="预览模式不产生网络写入",
        ),
        VideoListData(videos=[VideoItem(id="v1", name="第1讲", completed=True)]),
        VideoPlanSummary(
            total=1,
            completed=1,
            failed=0,
            results=[VideoTargetResult(video_id="v1", course_id="c1", initial_rate=0.0, final_rate=1.0, duration_seconds=1.5)],
        ),
        VideoProgress(course_id="c1", video_id="v1", current_rate=0.5, completed=False),
    ]


@pytest.mark.parametrize("data", _result_samples(), ids=lambda item: type(item).__name__)
def test_every_result_model_has_dedicated_renderer(data: object) -> None:
    """每个结果模型都命中专属渲染函数，不能落入 JSON 兜底。"""
    md = format_result_markdown(data)

    assert md.strip()
    assert not md.lstrip().startswith("```json")
    assert md.lstrip().startswith("##")


def test_table_cells_escape_pipe_and_newline() -> None:
    """平台文案中的竖线与换行不得破坏表格列结构。"""
    md = format_result_markdown(CourseListData(courses=[CourseItem(id="c1", name="第1章 | 导论\n附注", platform="ykt", classroom_id=1)]))
    row = next(line for line in md.splitlines() if "第1章" in line)

    assert "第1章 \\| 导论 附注" in row
    assert row.replace("\\|", "").count("|") == 6


def test_empty_collection_renders_hint_instead_of_empty_table() -> None:
    """空数据集给出显式提示。"""
    md = format_result_markdown(VideoListData(videos=[]))

    assert "共 0 个" in md
    assert "> 暂无数据" in md


def test_pagination_hint_renders_cursor_and_range() -> None:
    """分页元数据转化为可执行的 --cursor 提示。"""
    md = format_result_markdown(
        VideoListData(videos=[VideoItem(id="v1", name="第1讲", completed=False)]),
        meta={"limit": 5, "offset": 10, "total": 30, "has_more": True, "next_cursor": "15"},
    )

    assert "显示第 11~15 条" in md
    assert "共 30 条" in md
    assert "--cursor 15" in md


def test_error_details_render_as_json_not_python_repr() -> None:
    """错误详情必须是合法 JSON，而不是 Python repr。"""
    md = format_error_markdown("E1", "提交失败", {"field": "x", "flag": True})

    assert '`{"field":"x","flag":true}`' in md
    assert "'field'" not in md


def test_unregistered_payload_falls_back_safely() -> None:
    """未登记模型走 JSON 兜底；不可序列化对象降级为文本而不是抛错。"""
    assert format_result_markdown({"a": 1}).lstrip().startswith("```json")
    assert "无法结构化渲染" in format_result_markdown(object())


def test_next_actions_without_argv_renders_description() -> None:
    """当 NextAction 没有 argv 时，优雅展示 description 且不输出空反引号。"""
    from wkhelper.cli.envelope import NextAction
    from wkhelper.core.results import ExamListData

    action = NextAction(reason="请自主完成", argv=[], description="登录官网考场页作答")
    md = format_result_markdown(ExamListData(exams=[]), next_actions=[action])
    assert "- **请自主完成**：登录官网考场页作答" in md
    assert ": ``" not in md
    assert "：``" not in md
