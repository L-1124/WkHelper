"""考场页面作答请求捕获的纯逻辑回归。"""

import json
from typing import Any, cast

import pytest

from wkhelper.exam.browser import (
    ExamPageClient,
    _select_page,
    extract_answer_submission,
    wait_for_answer_submission,
)


def _events(post_data: str | None, problem_id: int, status: int = 200, url_suffix: str = "answer_problem") -> list[dict[str, Any]]:
    return [
        {
            "method": "Network.requestWillBeSent",
            "params": {
                "requestId": "r1",
                "request": {
                    "method": "POST",
                    "url": f"https://examination.test/exam_room/{url_suffix}",
                    "postData": post_data
                    if post_data is not None
                    else json.dumps({"results": [{"problem_id": problem_id, "result": ["A"]}], "exam_id": 42}),
                },
            },
        },
        {"method": "Network.responseReceived", "params": {"requestId": "r1", "response": {"status": status}}},
    ]


def test_select_page_prefers_exam_origin() -> None:
    """多标签页时只接受考场域名下的页面。"""
    tabs = [
        {"type": "page", "url": "https://example.test/home", "webSocketDebuggerUrl": "ws://a"},
        {"type": "page", "url": "https://examination.test/exam/42", "webSocketDebuggerUrl": "ws://b"},
        {"type": "service_worker", "url": "https://examination.test/sw.js", "webSocketDebuggerUrl": "ws://c"},
    ]
    assert _select_page(tabs, "https://examination.test") == "ws://b"


def test_select_page_refuses_foreign_origin() -> None:
    """没有考场页面时返回 None，禁止回落到其它标签页。"""
    tabs = [{"type": "page", "url": "https://example.test/home", "webSocketDebuggerUrl": "ws://a"}]
    assert _select_page(tabs, "https://examination.test") is None
    assert _select_page({}, "https://examination.test") is None


def test_select_page_rejects_lookalike_host() -> None:
    """相似域名（exam.test.evil）不能冒充考场主机。"""
    tabs = [
        {"type": "page", "url": "https://examination.test.evil/exam/42", "webSocketDebuggerUrl": "ws://evil"},
        {"type": "page", "url": "https://cdn.test/examination.test", "webSocketDebuggerUrl": "ws://path"},
    ]
    assert _select_page(tabs, "https://examination.test") is None


def test_extract_submission_matches_problem() -> None:
    """从页面请求体中取出目标题目的作答与响应。"""
    request, response = extract_answer_submission(_events(None, 201478531), "201478531")
    assert request is not None
    assert request["answer"] == ["A"]
    assert response is not None
    assert response["status"] == 200


def test_extract_submission_ignores_other_problem() -> None:
    """其它题目的作答请求不能作为本题证据。"""
    request, response = extract_answer_submission(_events(None, 201478531), "9001")
    assert request is None
    assert response is None


def test_extract_submission_skips_malformed_payload() -> None:
    """请求体不可解析或不是作答接口时忽略。"""
    assert extract_answer_submission(_events("{not json", 201478531), "201478531") == (None, None)
    assert extract_answer_submission(_events(None, 201478531, url_suffix="cover"), "201478531") == (None, None)


class _FakeClient:
    def __init__(self, events: list[dict[str, Any]], body: str | None = None, should_fail: bool = False) -> None:
        self.events = events
        self._body = body
        self._should_fail = should_fail

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 3.0) -> dict[str, Any]:
        del params, timeout
        if self._should_fail:
            raise RuntimeError("CDP call failed")
        if method == "Network.getResponseBody":
            return {"body": self._body} if self._body is not None else {}
        return {}


@pytest.mark.asyncio
async def test_wait_submission_explicit_success() -> None:
    """业务返回明确成功码时判定为 accepted。"""
    events = _events(None, 201478531)
    client = cast(ExamPageClient, _FakeClient(events, body=json.dumps({"success": True, "errcode": 0})))
    res = await wait_for_answer_submission(client, "201478531", since=0)
    assert res["observed"] is True
    assert res["accepted"] is True
    assert res["rejected"] is False
    assert res["unknown"] is False
    assert res["answer"] == ["A"]


@pytest.mark.asyncio
async def test_wait_submission_errcode_zero_success() -> None:
    """业务返回 errcode 0 时判定为 accepted。"""
    events = _events(None, 201478531)
    client = cast(ExamPageClient, _FakeClient(events, body=json.dumps({"errcode": 0})))
    res = await wait_for_answer_submission(client, "201478531", since=0)
    assert res["observed"] is True
    assert res["accepted"] is True
    assert res["rejected"] is False
    assert res["unknown"] is False


@pytest.mark.asyncio
async def test_wait_submission_html_body_is_unknown() -> None:
    """HTTP 200 HTML 网关/错误页判定为未知，不得判定为 accepted。"""
    events = _events(None, 201478531)
    client = cast(ExamPageClient, _FakeClient(events, body="<html><head><title>Bad Gateway</title></head></html>"))
    res = await wait_for_answer_submission(client, "201478531", since=0)
    assert res["observed"] is True
    assert res["accepted"] is False
    assert res["rejected"] is False
    assert res["unknown"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["{}", '{"success":true}', '{"code":0}', '{"errcode":false}', '{"errcode":0.5}'])
async def test_wait_submission_unconfirmed_business_response_is_unknown(body: str) -> None:
    """只有考场协议的有效成功码才能确认作答，通用字段和畸形码不能代替。"""
    events = _events(None, 201478531)
    client = cast(ExamPageClient, _FakeClient(events, body=body))
    res = await wait_for_answer_submission(client, "201478531", since=0)
    assert res["observed"] is True
    assert res["accepted"] is False
    assert res["rejected"] is False
    assert res["unknown"] is True


@pytest.mark.asyncio
async def test_wait_submission_read_failure_is_unknown() -> None:
    """CDP 读取响应体失败时判定为未知。"""
    events = _events(None, 201478531)
    client = cast(ExamPageClient, _FakeClient(events, should_fail=True))
    res = await wait_for_answer_submission(client, "201478531", since=0)
    assert res["observed"] is True
    assert res["accepted"] is False
    assert res["rejected"] is False
    assert res["unknown"] is True


@pytest.mark.asyncio
async def test_wait_submission_explicit_rejection_success_false() -> None:
    """平台返回 success: false 时判定为明确拒绝。"""
    events = _events(None, 201478531)
    client = cast(ExamPageClient, _FakeClient(events, body=json.dumps({"success": False, "msg": "考试已结束"})))
    res = await wait_for_answer_submission(client, "201478531", since=0)
    assert res["observed"] is True
    assert res["accepted"] is False
    assert res["rejected"] is True
    assert res["unknown"] is False


@pytest.mark.asyncio
async def test_wait_submission_explicit_rejection_errcode() -> None:
    """平台返回业务错误码时判定为明确拒绝。"""
    events = _events(None, 201478531)
    client = cast(ExamPageClient, _FakeClient(events, body=json.dumps({"errcode": 10001, "errmsg": "令牌已失效"})))
    res = await wait_for_answer_submission(client, "201478531", since=0)
    assert res["observed"] is True
    assert res["accepted"] is False
    assert res["rejected"] is True
    assert res["unknown"] is False


@pytest.mark.asyncio
async def test_wait_submission_http_500_is_rejected() -> None:
    """HTTP 500 判定为明确拒绝。"""
    events = _events(None, 201478531, status=500)
    client = cast(ExamPageClient, _FakeClient(events))
    res = await wait_for_answer_submission(client, "201478531", since=0)
    assert res["observed"] is True
    assert res["accepted"] is False
    assert res["rejected"] is True
    assert res["unknown"] is False
