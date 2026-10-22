"""考场前台浏览器 CDP 模拟点击驱动器。

在真实打开的考场页面中模拟人类点击选项与输入文本，
触发网页自身的数据绑定与自动暂存，杜绝无浏览器脱机发包特征。
"""

import asyncio
import functools
import importlib.resources
import json
import logging
import urllib.request
from typing import Any
from urllib.parse import urlparse

from wkhelper.domain.errors import APIError
from wkhelper.exam.session import ExamBrowserSession
from wkhelper.infra.browser.cdp import CdpClient
from wkhelper.infra.http import AsyncSession, build_session

logger = logging.getLogger(__name__)

_EVAL_TIMEOUT = 20.0
_ANSWER_ENDPOINT = "answer_problem"


@functools.cache
def _get_script(name: str) -> str:
    """读取并缓存 exam/scripts 下的 JS 资源。"""
    resource = importlib.resources.files("wkhelper.exam").joinpath("scripts", name)
    try:
        return resource.read_text(encoding="utf-8")
    except Exception as exc:
        raise RuntimeError(f"考场页面脚本缺失: {name}，请检查本地安装完整性") from exc


class ExamPageClient:
    """拥有 WebSocket 与底层 HTTP 会话的考场页面客户端。"""

    def __init__(self, http: AsyncSession, cdp: CdpClient) -> None:
        self._http = http
        self._cdp = cdp

    @property
    def events(self) -> list[dict[str, Any]]:
        """已接收的 CDP 网络事件。"""
        return self._cdp.events

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any]:
        """转发 CDP 调用。"""
        return await self._cdp.call(method, params, timeout)

    async def close(self) -> None:
        """释放 WebSocket 与底层会话，避免连接泄漏。"""
        try:
            await self._cdp.close()
        except Exception:
            logger.debug("关闭考场 CDP WebSocket 失败", exc_info=True)
        finally:
            try:
                await self._http.close()
            except Exception:
                logger.debug("关闭考场 CDP 会话失败", exc_info=True)


def _http_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=2) as resp:
        return json.loads(resp.read())


def _page_host(url: str) -> str:
    """提取 URL 的主机名，解析失败时返回空串。"""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _select_page(tabs: Any, host: str) -> str | None:
    """只接受考场域名下的页面目标，避免误连其它标签页。"""
    if not isinstance(tabs, list):
        return None
    host_name = (urlparse(host).hostname or "").lower()
    pages = [tab for tab in tabs if isinstance(tab, dict) and tab.get("type") == "page" and tab.get("webSocketDebuggerUrl")]
    for tab in pages:
        if host_name and _page_host(str(tab.get("url", ""))) == host_name:
            return str(tab["webSocketDebuggerUrl"])
    return None


async def connect_exam_page(session: ExamBrowserSession, timeout: float = 10.0) -> ExamPageClient:
    """连接考场浏览器会话中的考场页面目标。"""
    deadline = asyncio.get_running_loop().time() + timeout
    page_ws: str | None = None

    while asyncio.get_running_loop().time() < deadline:
        try:
            tabs = await asyncio.to_thread(_http_json, f"http://127.0.0.1:{session.port}/json/list")
            page_ws = _select_page(tabs, session.host)
        except Exception:
            page_ws = None
        if page_ws:
            break
        await asyncio.sleep(0.3)

    if not page_ws:
        raise APIError(
            f"无法连接到考场浏览器考场页面（端口 {session.port}，域名 {session.host}）。"
            "请确认考场浏览器窗口仍在运行，必要时重新执行 `exam start --confirm`。",
            retryable=True,
        )

    http = build_session(timeout=15.0, proxy=None, user_agent=None, impersonate=None)
    try:
        ws = await http.ws_connect(page_ws)
    except Exception:
        await http.close()
        raise
    client = ExamPageClient(http, CdpClient(ws))
    try:
        await client.call(
            "Network.enable",
            {
                "maxTotalBufferSize": 10485760,
                "maxResourceBufferSize": 5242880,
                "enableDurableMessages": True,
            },
        )
    except Exception:
        logger.debug("考场页面开启 Network 监听失败，作答提交将退回暂存回读校验", exc_info=True)
    return client


async def begin_browser_exam(session: ExamBrowserSession, timeout: float = 20.0) -> None:
    """点击网页开始按钮，并同时核验本场业务响应与已加载题目的答题页。"""
    probe_script = _get_script("begin.js")
    probe = f"({probe_script})({{}})"
    ready_script = _get_script("ready.js")
    ready_args = {"eid": str(session.eid)}
    ready_probe = f"({ready_script})({json.dumps(ready_args, ensure_ascii=False)})"
    client = await connect_exam_page(session)
    deadline = asyncio.get_running_loop().time() + timeout
    baseline = len(client.events)
    clicked = False
    request_id: str | None = None
    accepted = False
    try:
        # Network 监听失败必须在点击前终止，不能无证据地开始计时。
        # 显式开启 enableDurableMessages 并在渲染器外保存响应体，防止立即跳转导航导致 getResponseBody 丢失。
        await client.call(
            "Network.enable",
            {
                "maxTotalBufferSize": 10485760,
                "maxResourceBufferSize": 5242880,
                "enableDurableMessages": True,
            },
        )
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            if not clicked:
                result = await client.call("Runtime.evaluate", {"expression": probe, "returnByValue": True}, timeout=remaining)
                clicked = result.get("result", {}).get("value") is True
            events = client.events[baseline:]
            for event in events:
                params = event.get("params") or {}
                if event.get("method") == "Network.requestWillBeSent" and request_id is None:
                    request = params.get("request") or {}
                    url = urlparse(str(request.get("url", "")))
                    if (
                        request.get("method") != "POST"
                        or url.path != "/exam_room/start_paper"
                        or url.netloc != urlparse(session.host).netloc
                    ):
                        continue
                    try:
                        payload = json.loads(request.get("postData") or "{}")
                    except (ValueError, TypeError):
                        continue
                    if isinstance(payload, dict) and str(payload.get("exam_id")) == str(session.eid):
                        request_id = params.get("requestId")
                if event.get("method") == "Network.responseReceived" and request_id and not accepted:
                    if params.get("requestId") != request_id:
                        continue
                    status = (params.get("response") or {}).get("status", 0)
                    if not 200 <= status < 300:
                        raise APIError(f"网页开始答题失败（HTTP {status}）", retryable=False)
                    if not any(
                        e.get("method") == "Network.loadingFinished" and (e.get("params") or {}).get("requestId") == request_id
                        for e in events
                    ):
                        continue
                    body = await client.call("Network.getResponseBody", {"requestId": request_id}, timeout=max(0.01, remaining))
                    try:
                        payload = json.loads(body.get("body") or "{}")
                    except (ValueError, TypeError) as exc:
                        raise APIError("网页开始答题响应无法确认，请在浏览器核对；不要重复开始", retryable=False) from exc
                    if (
                        not isinstance(payload, dict)
                        or isinstance(payload.get("errcode"), bool)
                        or payload.get("errcode") not in (0, "0")
                        or payload.get("success") is False
                    ):
                        raise APIError("网页开始答题未获平台成功确认，请在浏览器核对", retryable=False)
                    accepted = True
            if accepted:
                result = await client.call(
                    "Runtime.evaluate",
                    {"expression": ready_probe, "returnByValue": True},
                    timeout=max(0.01, deadline - asyncio.get_running_loop().time()),
                )
                if result.get("result", {}).get("value") is True:
                    return
            await asyncio.sleep(0.1)
        raise APIError(
            "未确认本场开始响应及题目加载；请在浏览器核对开始按钮、提示框或验证要求，不要重复开始",
            retryable=False,
        )
    finally:
        await client.close()


def extract_answer_submission(events: list[dict[str, Any]], problem_id: str | int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """从 CDP 事件中取出页面自身的作答请求与该请求的响应。"""
    target = str(problem_id)
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    for event in events:
        method = event.get("method")
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        if method == "Network.requestWillBeSent":
            raw_request = params.get("request") if isinstance(params.get("request"), dict) else {}
            if str(raw_request.get("method", "")).upper() != "POST" or _ANSWER_ENDPOINT not in str(raw_request.get("url", "")):
                continue
            try:
                payload = json.loads(raw_request.get("postData") or "{}")
            except ValueError:
                continue
            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list):
                continue
            hit = next(
                (item for item in results if isinstance(item, dict) and str(item.get("problem_id")) == target),
                None,
            )
            if hit is None:
                continue
            raw_answer = hit.get("result")
            if isinstance(raw_answer, list):
                answers = [str(value) for value in raw_answer if str(value).strip()]
            elif raw_answer is None or str(raw_answer).strip() == "":
                answers = []
            else:
                answers = [str(raw_answer)]
            request = {"request_id": params.get("requestId"), "answer": answers}
        elif method == "Network.responseReceived" and request is not None:
            if params.get("requestId") == request["request_id"]:
                response = params.get("response") if isinstance(params.get("response"), dict) else {}
    return request, response


async def wait_for_answer_submission(
    cdp: ExamPageClient,
    problem_id: str | int,
    timeout: float = 8.0,
    since: int | None = None,
) -> dict[str, Any]:
    """等待页面自身发出的作答请求及其响应，作为落库证据。"""
    start = len(cdp.events) if since is None else since
    deadline = asyncio.get_running_loop().time() + timeout
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None

    while asyncio.get_running_loop().time() < deadline:
        request, response = extract_answer_submission(cdp.events[start:], problem_id)
        if request is not None and response is not None:
            break
        await asyncio.sleep(0.2)

    if request is None:
        return {"observed": False, "accepted": False, "rejected": False, "unknown": True, "answer": []}

    status = response.get("status") if isinstance(response, dict) else None
    errcode: int | None = None
    accepted = False
    rejected = False
    unknown = False

    if not isinstance(status, int):
        unknown = True
    elif status >= 400:
        rejected = True
    else:
        try:
            body = await cdp.call("Network.getResponseBody", {"requestId": request["request_id"]}, timeout=3.0)
            text = body.get("body") if isinstance(body, dict) else None
            if not isinstance(text, str) or not text.strip():
                unknown = True
            else:
                try:
                    parsed = json.loads(text)
                except (ValueError, TypeError):
                    parsed = None
                    unknown = True

                if isinstance(parsed, dict):
                    raw_errcode = parsed.get("errcode")
                    if isinstance(raw_errcode, (int, str)) and not isinstance(raw_errcode, bool):
                        try:
                            errcode = int(raw_errcode)
                        except ValueError:
                            pass
                    rejected = parsed.get("success") is False or (errcode is not None and errcode != 0)
                    accepted = 200 <= status < 300 and errcode == 0 and not rejected
                    unknown = not accepted and not rejected
                else:
                    unknown = True
        except Exception:
            logger.debug("读取作答响应体失败，状态归为未知", exc_info=True)
            unknown = True

    return {
        "observed": True,
        "accepted": accepted,
        "rejected": rejected,
        "unknown": unknown,
        "answer": request["answer"],
        "status": status,
        "errcode": errcode,
    }


async def simulate_browser_answer(
    cdp: ExamPageClient,
    problem_id: str | int,
    answer_keys: list[str],
    mode: str = "click",
) -> dict[str, Any]:
    """在考场页面中定位指定题目并模拟点击或输入，返回页面回读状态。"""
    script = _get_script("answer.js")
    args = {
        "pid": str(problem_id),
        "keys": [str(key) for key in answer_keys],
        "mode": mode,
    }
    expression = f"({script})({json.dumps(args, ensure_ascii=False)})"
    res = await cdp.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        timeout=_EVAL_TIMEOUT,
    )
    value = res.get("result", {}).get("value")
    if not isinstance(value, dict):
        return {"success": False, "error": "考场页面未返回有效执行结果"}
    return value
