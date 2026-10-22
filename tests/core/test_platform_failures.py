"""平台业务确认与统一错误分类回归。"""

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from wkhelper.article.service import read_article
from wkhelper.domain.courses import Course
from wkhelper.domain.errors import APIError, AuthError, RiskControlError
from wkhelper.infra.http import ConnectionError as HttpConnectionError
from wkhelper.infra.http import HTTPError


@pytest.mark.asyncio
async def test_article_rejection_does_not_claim_completion(fake_platform, monkeypatch) -> None:
    async def articles(*args):
        return []

    async def mark(*args):
        return False

    monkeypatch.setattr("wkhelper.article.service.get_articles", articles)
    monkeypatch.setattr(fake_platform, "mark_article_finish", mark)
    with pytest.raises(APIError, match="标记完成失败"):
        await read_article(fake_platform, "leaf", Course(id="c", name="course", platform_id="test"))


@pytest.mark.asyncio
async def test_article_cli_propagates_platform_rejection(fake_platform, monkeypatch, capsys) -> None:
    from wkhelper.main import async_main

    async def articles(*args):
        return []

    async def mark(*args):
        return False

    @asynccontextmanager
    async def open_course(*args):
        yield fake_platform, Course(id="c", name="course", platform_id="test")

    monkeypatch.setattr("wkhelper.article.service.get_articles", articles)
    monkeypatch.setattr(fake_platform, "mark_article_finish", mark)
    monkeypatch.setattr("wkhelper.cli.commands.article._open_course", open_course)
    code = await async_main(["--format", "json", "article", "read", "-c", "c", "-a", "leaf", "--confirm"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 6
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "PLATFORM_ERROR"


class _Response:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status_code = status
        self.payload = payload
        self.text = json.dumps(payload)
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError("rejected")

    def json(self) -> dict[str, Any]:
        return self.payload


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 403])
async def test_submission_preserves_risk_control_classification(fake_platform, monkeypatch, status: int) -> None:
    async def request(*args, **kwargs):
        return _Response(status, {"error_code": 400403})

    monkeypatch.setattr(fake_platform.client, "request", request)
    with pytest.raises(RiskControlError):
        await fake_platform.submit_answer("homework", 1, ["A"])


@pytest.mark.asyncio
async def test_submission_preserves_auth_classification(fake_platform, monkeypatch) -> None:
    async def request(*args, **kwargs):
        return _Response(403, {"detail": "forbidden"})

    monkeypatch.setattr(fake_platform.client, "request", request)
    with pytest.raises(AuthError):
        await fake_platform.submit_answer("homework", 1, ["A"])


@pytest.mark.asyncio
async def test_submission_connection_failure_is_platform_error(fake_platform, monkeypatch) -> None:
    async def request(*args, **kwargs):
        raise HttpConnectionError("offline")

    monkeypatch.setattr(fake_platform.client, "request", request)
    with pytest.raises(APIError, match="网络请求失败"):
        await fake_platform.submit_answer("homework", 1, ["A"])
