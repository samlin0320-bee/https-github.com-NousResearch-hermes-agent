import asyncio
import json

import pytest

from gateway.session_context import (
    clear_session_vars,
    get_session_env,
    run_in_executor_with_context,
    set_session_vars,
)
from tools.process_registry import process_registry
from tools.terminal_tool import terminal_tool


@pytest.mark.asyncio
async def test_run_in_executor_with_context_propagates_gateway_session_vars():
    tokens = set_session_vars(
        platform="feishu",
        chat_id="chat-123",
        chat_name="dm",
        thread_id="thread-7",
        user_id="user-9",
        user_name="Master",
        session_key="sess-abc",
    )
    try:
        loop = asyncio.get_running_loop()

        def read_session_vars():
            return {
                "platform": get_session_env("HERMES_SESSION_PLATFORM"),
                "chat_id": get_session_env("HERMES_SESSION_CHAT_ID"),
                "thread_id": get_session_env("HERMES_SESSION_THREAD_ID"),
                "user_id": get_session_env("HERMES_SESSION_USER_ID"),
                "user_name": get_session_env("HERMES_SESSION_USER_NAME"),
                "session_key": get_session_env("HERMES_SESSION_KEY"),
            }

        result = await run_in_executor_with_context(loop, read_session_vars)

        assert result == {
            "platform": "feishu",
            "chat_id": "chat-123",
            "thread_id": "thread-7",
            "user_id": "user-9",
            "user_name": "Master",
            "session_key": "sess-abc",
        }
    finally:
        clear_session_vars(tokens)


@pytest.mark.asyncio
async def test_terminal_background_notify_registers_watcher_with_session_context():
    tokens = set_session_vars(
        platform="feishu",
        chat_id="chat-xyz",
        thread_id="thread-42",
        user_id="user-9",
        user_name="Master",
        session_key="sess-bg",
    )
    session_id = None
    try:
        loop = asyncio.get_running_loop()

        def launch_background():
            return terminal_tool(
                command="python -c 'import time; time.sleep(0.1)'",
                background=True,
                task_id="ctx-bg",
                notify_on_complete=True,
            )

        raw = await run_in_executor_with_context(loop, launch_background)
        data = json.loads(raw)
        session_id = data["session_id"]
        assert data["notify_on_complete"] is True

        watcher = next(w for w in process_registry.pending_watchers if w["session_id"] == session_id)
        assert watcher["platform"] == "feishu"
        assert watcher["chat_id"] == "chat-xyz"
        assert watcher["thread_id"] == "thread-42"
        assert watcher["user_id"] == "user-9"
        assert watcher["user_name"] == "Master"
    finally:
        clear_session_vars(tokens)
        if session_id:
            process_registry.wait(session_id, timeout=2)
