"""输出格式选择与 TTY 智能探测测试。"""

import json
from io import StringIO

import pytest

from wkhelper.cli.markdown import format_result_markdown
from wkhelper.core.models import (
    ArticleItem,
    ArticleListData,
    CourseItem,
    CourseListData,
    DiscussionItem,
    DiscussionListData,
    HomeworkItem,
    HomeworkListData,
    VideoItem,
    VideoListData,
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


def test_markdown_formatters_for_domain_models():
    """验证领域结果模型渲染为 GFM 表格。"""
    courses_md = format_result_markdown(CourseListData(courses=[CourseItem(id="c1", name="测试课程", platform="ykt", classroom_id=123)]))
    assert "| 1 | c1 | 测试课程 | ykt | 123 |" in courses_md
    videos_md = format_result_markdown(VideoListData(videos=[VideoItem(id="v1", name="第1讲", completed=True)]))
    assert "已完成" in videos_md

    hw_md = format_result_markdown(HomeworkListData(homeworks=[HomeworkItem(id="h1", name="第1次作业", completed=False)]))
    assert "未完成" in hw_md

    articles_md = format_result_markdown(ArticleListData(articles=[ArticleItem(id="a1", name="导学材料", completed=True)]))
    assert "已读" in articles_md

    disc_md = format_result_markdown(DiscussionListData(discussions=[DiscussionItem(id="d1", name="互动交流", completed=False)]))
    assert "未发言" in disc_md
