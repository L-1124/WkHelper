"""考场页面作答请求捕获的纯逻辑回归。"""

import json
from typing import Any

from wkhelper.core.exam_browser_driver import _select_page, extract_answer_submission


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
