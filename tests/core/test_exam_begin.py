"""浏览器开始答题的业务响应及题目就绪边界。"""

import json
from typing import Any

import pytest

from wkhelper.domain.errors import APIError
from wkhelper.exam.browser import begin_browser_exam
from wkhelper.exam.session import ExamBrowserSession


class StartPage:
    def __init__(self, response: str, *, ready: bool = True, eid: int = 42, host: str = "https://exam.test") -> None:
        self.response = response
        self.ready = ready
        self.eid = eid
        self.host = host
        self.events: list[dict[str, Any]] = []
        self.closed = False
        self.clicked = False

    async def call(self, method: str, params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        if method == "Network.getResponseBody":
            return {"body": self.response}
        if method == "Runtime.evaluate":
            if not self.clicked:
                self.clicked = True
                self.events.extend(
                    [
                        {
                            "method": "Network.requestWillBeSent",
                            "params": {
                                "requestId": "start",
                                "request": {
                                    "url": f"{self.host}/exam_room/start_paper",
                                    "method": "POST",
                                    "postData": json.dumps({"exam_id": self.eid}),
                                },
                            },
                        },
                        {"method": "Network.responseReceived", "params": {"requestId": "start", "response": {"status": 200}}},
                        {"method": "Network.loadingFinished", "params": {"requestId": "start"}},
                    ]
                )
                return {"result": {"value": True}}
            return {"result": {"value": self.ready}}
        return {}

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response", ["{}", '{"errcode":false}', "<html>blocked</html>", '{"errcode":10103}', '{"errcode":0,"success":false}']
)
async def test_begin_rejects_unconfirmed_or_failed_business_response(monkeypatch, response: str) -> None:
    page = StartPage(response)

    async def connect(session):
        return page

    monkeypatch.setattr("wkhelper.exam.browser.connect_exam_page", connect)
    with pytest.raises(APIError):
        await begin_browser_exam(ExamBrowserSession("leaf", "ykt", "https://exam.test", 42, 1, "", ""), timeout=0.2)
    assert page.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ready,eid,host", [(False, 42, "https://exam.test"), (True, 43, "https://exam.test"), (True, 42, "https://other.test")]
)
async def test_begin_requires_matching_exam_response_and_loaded_questions(monkeypatch, ready: bool, eid: int, host: str) -> None:
    page = StartPage('{"errcode":0}', ready=ready, eid=eid, host=host)

    async def connect(session):
        return page

    monkeypatch.setattr("wkhelper.exam.browser.connect_exam_page", connect)
    with pytest.raises(APIError):
        await begin_browser_exam(ExamBrowserSession("leaf", "ykt", "https://exam.test", 42, 1, "", ""), timeout=0.15)
    assert page.closed


@pytest.mark.asyncio
async def test_begin_confirms_success_only_after_questions_load(monkeypatch) -> None:
    page = StartPage('{"errcode":0}')

    async def connect(session):
        return page

    monkeypatch.setattr("wkhelper.exam.browser.connect_exam_page", connect)
    await begin_browser_exam(ExamBrowserSession("leaf", "ykt", "https://exam.test", 42, 1, "", ""), timeout=0.3)
    assert page.closed
