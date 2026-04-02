"""ByteRover memory bridge — translates Hermes memory tool writes to brv curate.

Used by ``run_agent.py`` to sync memory tool operations (add/replace/remove)
to ByteRover's context tree in a fire-and-forget daemon thread.
"""

import logging

from byterover_integration.client import run_brv_curate_sync, BRV_CURATE_TIMEOUT

logger = logging.getLogger(__name__)

__all__ = ["build_brv_memory_content", "brv_curate_fire_and_forget"]


def build_brv_memory_content(action: str, target: str, content: str = None) -> str:
    """Build curate content string for the ByteRover memory bridge.

    For add/replace, prefixes the content with a target label so ByteRover
    can distinguish user profile facts from agent notes.
    For remove, returns a single space — curating whitespace effectively
    clears the entry in the context tree.
    """
    if action == "remove":
        return " "
    label = "User profile" if target == "user" else "Agent memory"
    return f"[{label}] {content}"


def brv_curate_fire_and_forget(content: str) -> None:
    """Fire-and-forget ByteRover curate — runs in daemon thread."""
    try:
        result = run_brv_curate_sync(["curate", "--", content], timeout=BRV_CURATE_TIMEOUT)
        if not result["success"]:
            logger.warning("[brv-curate] Fire-and-forget failed: %s", result.get("error", "unknown"))
    except Exception as e:
        logger.debug("[brv-curate] Fire-and-forget error: %s", e)
