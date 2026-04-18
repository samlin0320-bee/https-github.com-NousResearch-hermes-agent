import asyncio

import pytest

from gateway.session_context import _UNSET, _VAR_MAP
import tools.approval as approval_module


def _reset_approval_module_state() -> None:
    for attr in (
        "_gateway_queues",
        "_gateway_notify_cbs",
        "_session_approved",
        "_permanent_approved",
        "_pending",
        "_session_yolo",
    ):
        try:
            getattr(approval_module, attr).clear()
        except Exception:
            pass
    approval_module._approval_session_key.set("")


@pytest.fixture(autouse=True)
def _reset_shared_contextvars(tmp_path, monkeypatch):
    """Reset cross-test contextvars that otherwise leak within one thread."""
    hermes_home = tmp_path / "hermes_test_home"
    hermes_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    created_loop = None
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        created_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(created_loop)

    for var in _VAR_MAP.values():
        var.set(_UNSET)
    _reset_approval_module_state()
    yield
    for var in _VAR_MAP.values():
        var.set(_UNSET)
    _reset_approval_module_state()
    if created_loop is not None:
        try:
            pending = [task for task in asyncio.all_tasks(created_loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                created_loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except Exception:
            pass
        finally:
            asyncio.set_event_loop(None)
            created_loop.close()
