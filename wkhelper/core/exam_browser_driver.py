"""考场前台浏览器 CDP 模拟点击驱动器。

在真实打开的考场页面中模拟人类点击选项与输入文本，
触发网页自身的数据绑定与自动暂存，杜绝无浏览器脱机发包特征。
"""

import asyncio
import json
import logging
import urllib.request
from typing import Any
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession

from wkhelper.core.browser_manager import _CdpClient
from wkhelper.core.exam_session import ExamBrowserSession
from wkhelper.core.exceptions import APIError

logger = logging.getLogger(__name__)

_EVAL_TIMEOUT = 20.0
_ANSWER_ENDPOINT = "answer_problem"

# 通过 Vue 组件实例定位目标题目：页面 DOM 只带序号 id，题目 ID 仅存在于组件 props。
# 点击/输入之后等待 $nextTick，再读回组件状态作为校验，绝不跨题兜底。
_SIMULATE_JS = """
(async () => {
    const pid = __PID__;
    const keys = __KEYS__;
    const mode = __MODE__;

    const norm = (v) => String(v == null ? '' : v).trim().toUpperCase();

    const findInstance = (root) => {
        const stack = [root];
        while (stack.length) {
            const cur = stack.pop();
            const item = cur.$props ? cur.$props.item : null;
            if (item && item.problem_id !== undefined && String(item.problem_id) === pid) return cur;
            const children = cur.$children || [];
            for (let i = 0; i < children.length; i += 1) stack.push(children[i]);
        }
        return null;
    };

    const roots = Array.from(document.querySelectorAll('*')).filter((el) => el.__vue__);
    let target = null;
    for (const el of roots) {
        const vm = el.__vue__;
        if (!vm) continue;
        target = findInstance(vm.$root);
        if (target) break;
    }
    if (!target) return {success: false, error: '未找到题目 ' + pid + ' 的页面组件'};

    const item = target.$props.item;
    const el = target.$el;
    const type = Number(item.ProblemType);
    const tick = () => new Promise((resolve) => target.$nextTick(resolve));

    const setField = (node, value) => {
        node.scrollIntoView({block: 'center'});
        node.focus();
        if (node.tagName === 'TEXTAREA' || node.tagName === 'INPUT') {
            const proto = node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(node, value);
        } else {
            node.innerText = value;
        }
        node.dispatchEvent(new Event('input', {bubbles: true}));
        node.dispatchEvent(new Event('change', {bubbles: true}));
        node.dispatchEvent(new Event('blur', {bubbles: true}));
    };

    // UEditor 等富文本编辑器的可编辑区位于 iframe 内，必须跨文档查找
    const collectFields = (root) => {
        const selector = 'textarea, input[type="text"], [contenteditable="true"], .ql-editor';
        const found = Array.from(root.querySelectorAll(selector));
        Array.from(root.querySelectorAll('iframe')).forEach((frame) => {
            let doc = null;
            try { doc = frame.contentDocument; } catch (e) { return; }
            if (!doc || !doc.body) return;
            if (doc.body.isContentEditable) found.push(doc.body);
            found.push(...Array.from(doc.querySelectorAll(selector)));
        });
        return found;
    };

    if (mode === 'text' || type === 4 || type === 5) {
        const fields = collectFields(el);
        if (!fields.length) return {success: false, error: '题目 ' + pid + ' 未找到可输入控件'};
        if (type === 4 && keys.length > fields.length) {
            return {success: false, error: '题目 ' + pid + ' 填空数量与答案数量不匹配'};
        }
        if (type === 4 && keys.length < fields.length) {
            return {success: false, error: '题目 ' + pid + ' 有 ' + fields.length + ' 个空，但只提供了 ' + keys.length + ' 个答案'};
        }
        if (type === 4) {
            fields.forEach((field, index) => setField(field, keys[index] ?? ''));
        } else {
            setField(fields[0], keys.join('\\n'));
        }
        const readField = (node) => (node.tagName === 'TEXTAREA' || node.tagName === 'INPUT' ? node.value : node.innerText);
        const expected = type === 4 ? keys : [keys.join('\\n')];
        const written = (type === 4 ? fields.map(readField) : [readField(fields[0])]).map(norm);
        const verify = expected.map(norm);
        const landed = verify.every((value, index) => written[index] === value);
        if (!landed) {
            return {success: false, error: '题目 ' + pid + ' 输入内容未写入页面控件', written: written};
        }
        await tick();
        const blanks = Array.isArray(item.answerArr) ? item.answerArr.map(norm) : [];
        return {
            success: true,
            mode: 'input',
            problem_type: type,
            filled: type === 4 ? Math.min(keys.length, fields.length) : 1,
            actual: blanks,
        };
    }

    const labels = Array.from(el.querySelectorAll('label.el-radio, label.el-checkbox'));
    if (!labels.length) return {success: false, error: '题目 ' + pid + ' 未找到选项控件'};
    const optionOf = (label) => {
        const input = label.querySelector('input[type="radio"], input[type="checkbox"]');
        const value = norm(input ? input.value : '');
        const text = norm((label.querySelector('.radioInput, .checkboxInput') || label).innerText);
        return {label: label, id: value || text, text: text, value: value};
    };
    const checked = (label) => {
        const input = label.querySelector('input[type="radio"], input[type="checkbox"]');
        return !!(input && input.checked);
    };

    const options = labels.map(optionOf);
    // 判断题的选项键是 true/false，界面显示“正确/错误”，必须按选项键匹配
    const matches = (option, key) => option.value === key || option.text === key;
    const wanted = keys.map(norm);
    const missing = wanted.filter((key) => !options.some((option) => matches(option, key)));
    if (missing.length) return {success: false, error: '考场页面未找到选项: ' + missing.join(',')};
    const expected = wanted.map((key) => options.find((option) => matches(option, key)).id);

    // 每次点击都会触发组件重渲染，必须重新查询节点并等待渲染完成，否则后续点击落在已卸载的 label 上
    const liveOptions = () => Array.from(el.querySelectorAll('label.el-radio, label.el-checkbox')).map(optionOf);
    let clicked = 0;
    const clickOption = async (key) => {
        const option = liveOptions().find((item) => matches(item, key));
        if (!option) return false;
        if (checked(option.label)) return true;
        option.label.scrollIntoView({block: 'center'});
        option.label.click();
        clicked += 1;
        await tick();
        return true;
    };
    if (type === 1) {
        await clickOption(wanted[0]);
    } else {
        for (const key of wanted) {
            if (!await clickOption(key)) return {success: false, error: '考场页面未找到选项: ' + key};
        }
        // 清掉不在答案里的历史勾选
        for (const key of liveOptions().map((item) => item.id)) {
            if (wanted.includes(key)) continue;
            const extra = liveOptions().find((item) => item.id === key);
            if (extra && checked(extra.label)) {
                extra.label.click();
                clicked += 1;
                await tick();
            }
        }
    }

    const selected = liveOptions().filter((option) => checked(option.label)).map((option) => option.id);
    const matched = selected.length === expected.length && expected.every((key) => selected.includes(key));
    return {
        success: matched,
        mode: 'click',
        problem_type: type,
        clicked_count: clicked,
        selected: selected,
        error: matched ? null : '选项点击后状态不符: 期望 ' + expected.join(',') + ' 实际 ' + (selected.join(',') || '空'),
    };
})()
"""


class ExamPageClient:
    """拥有 WebSocket 与底层 HTTP 会话的考场页面客户端。"""

    def __init__(self, http: AsyncSession, cdp: _CdpClient) -> None:
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

    http = AsyncSession()
    try:
        ws = await http.ws_connect(page_ws)
    except Exception:
        await http.close()
        raise
    client = ExamPageClient(http, _CdpClient(ws))
    try:
        await client.call("Network.enable")
    except Exception:
        logger.debug("考场页面开启 Network 监听失败，作答提交将退回暂存回读校验", exc_info=True)
    return client


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
    expression = (
        _SIMULATE_JS.replace("__PID__", json.dumps(str(problem_id)))
        .replace("__KEYS__", json.dumps([str(key) for key in answer_keys], ensure_ascii=False))
        .replace("__MODE__", json.dumps(mode))
    )
    res = await cdp.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        timeout=_EVAL_TIMEOUT,
    )
    value = res.get("result", {}).get("value")
    if not isinstance(value, dict):
        return {"success": False, "error": "考场页面未返回有效执行结果"}
    return value
