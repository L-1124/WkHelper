"""调用上下文的隔离、恢复与配置约束。"""

import asyncio
import json
import logging

import pytest
from pydantic import ValidationError

from wkhelper.cli.context import SessionOptions, execution_context, get_context
from wkhelper.infra.events import is_debug_enabled
from wkhelper.main import async_main


def test_session_options_validate_cookie_mode_and_reject_coercion() -> None:
    assert SessionOptions(credential_mode="session", cookie_file="cookies.json").cookie_file == "cookies.json"
    with pytest.raises(ValidationError):
        SessionOptions(cookie_file="cookies.json")
    with pytest.raises(ValidationError):
        SessionOptions.model_validate({"debug": "true"})


def test_nested_context_restores_options_and_debug_on_failure() -> None:
    with execution_context(SessionOptions(proxy="http://outer", debug=True)) as outer:
        with pytest.raises(RuntimeError):
            with execution_context(SessionOptions(proxy="http://inner")):
                assert get_context().options.proxy == "http://inner"
                assert not is_debug_enabled()
                raise RuntimeError("failure")
        assert get_context() is outer
        assert get_context().options.proxy == "http://outer"
        assert is_debug_enabled()
    with pytest.raises(LookupError):
        get_context()
    assert not is_debug_enabled()


@pytest.mark.asyncio
async def test_cli_calls_isolate_context_and_debug_logging(monkeypatch, capsys) -> None:
    from wkhelper.cli import app as cli

    barrier = asyncio.Barrier(2)
    seen: dict[str, tuple[str | None, bool]] = {}

    async def dispatch(tokens):
        label = tokens[0]
        await barrier.wait()
        options = get_context().options
        seen[label] = (options.proxy, is_debug_enabled())
        logging.getLogger("wkhelper.context-test").debug("context-%s", label)
        return 0

    monkeypatch.setattr(cli, "dispatch", dispatch)
    with execution_context(SessionOptions(proxy="http://outer")) as outer:
        codes = await asyncio.gather(
            async_main(["--debug", "--proxy", "http://first", "first"]),
            async_main(["--proxy", "http://second", "second"]),
        )
        assert codes == [0, 0]
        assert seen == {"first": ("http://first", True), "second": ("http://second", False)}
        assert get_context() is outer
        assert not is_debug_enabled()
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    messages = [event["payload"]["message"] for event in events if event["event"] == "debug.log"]
    assert messages == ["context-first"]
    with pytest.raises(LookupError):
        get_context()


@pytest.mark.asyncio
async def test_cancelled_cli_call_restores_outer_context(monkeypatch) -> None:
    from wkhelper.cli import app as cli

    async def dispatch(tokens):
        raise asyncio.CancelledError

    monkeypatch.setattr(cli, "dispatch", dispatch)
    with execution_context(SessionOptions(proxy="http://outer", debug=True)) as outer:
        with pytest.raises(asyncio.CancelledError):
            await async_main(["--proxy", "http://cancelled", "cancel"])
        assert get_context() is outer
        assert is_debug_enabled()


@pytest.mark.asyncio
async def test_error_rendering_keeps_current_format_and_debug(capsys) -> None:
    with execution_context(SessionOptions(format="md", debug=True)) as outer:
        code = await async_main(["--format", "json", "--credential-mode", "session", "course", "inspect", "-c", "missing"])
        envelope = json.loads(capsys.readouterr().out)
        assert code == 3
        assert envelope["error"]["code"] == "AUTH_EXPIRED"
        assert envelope["error"]["details"] == {}
        assert get_context() is outer
        assert is_debug_enabled()
    with pytest.raises(LookupError):
        get_context()
