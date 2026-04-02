"""ByteRover recall mode, auto-enrichment, auto-curate, and auto-flush.

- ``get_brv_recall_mode()`` — reads ``byterover.recall_mode`` from config.yaml
- ``brv_auto_enrich()`` — queries brv for context relevant to user message
- ``brv_auto_curate_turn()`` — LLM-gated per-turn curation (background thread)
- ``brv_flush_on_compress()`` — curates insights before context compression
"""

import logging
import threading
import time
from typing import Optional

from byterover_integration.client import (
    check_requirements,
    get_hermes_home,
    run_brv,
    run_brv_curate_sync,
)

logger = logging.getLogger(__name__)

__all__ = [
    "get_brv_recall_mode",
    "brv_auto_enrich",
    "brv_auto_curate_turn",
    "brv_flush_on_compress",
    "_extract_text",
]

# ---------------------------------------------------------------------------
# Recall mode (read from config.yaml, cached with TTL)
# ---------------------------------------------------------------------------

_VALID_RECALL_MODES = frozenset({"hybrid", "context", "tools", "off"})

_recall_mode_lock = threading.Lock()
_cached_recall_mode: Optional[str] = None
_cached_recall_mode_time: float = 0.0
_RECALL_MODE_CACHE_TTL = 60.0  # seconds


def _read_recall_mode_from_disk() -> str:
    """Read ByteRover recall_mode from config.yaml on disk."""
    try:
        import yaml
        config_path = get_hermes_home() / "config.yaml"
        if config_path.exists():
            with open(config_path, "r") as f:
                cfg = yaml.safe_load(f) or {}
            raw = cfg.get("byterover", {}).get("recall_mode", "hybrid")
            # PyYAML parses bare 'off' as boolean False — map it back
            if raw is False:
                return "off"
            mode = (str(raw) if raw else "hybrid").lower().strip()
            return mode if mode in _VALID_RECALL_MODES else "hybrid"
    except ImportError:
        logger.debug("[brv-recall] yaml not available, using default recall_mode")
    except Exception as e:
        logger.debug("[brv-recall] Error reading config.yaml: %s", e)
    return "hybrid"


def get_brv_recall_mode() -> str:
    """Read ByteRover recall_mode from ~/.hermes/config.yaml.

    Returns one of: hybrid, context, tools, off.
    Defaults to 'hybrid' if not configured.
    Results are cached for up to 60 seconds to avoid disk I/O per turn.
    """
    global _cached_recall_mode, _cached_recall_mode_time
    now = time.monotonic()
    with _recall_mode_lock:
        if _cached_recall_mode is not None and (now - _cached_recall_mode_time) < _RECALL_MODE_CACHE_TTL:
            return _cached_recall_mode
    # Disk read outside lock
    mode = _read_recall_mode_from_disk()
    with _recall_mode_lock:
        _cached_recall_mode = mode
        _cached_recall_mode_time = now
    return mode


def _reset_recall_mode_cache() -> None:
    """Reset the cached recall mode (for testing)."""
    global _cached_recall_mode, _cached_recall_mode_time
    with _recall_mode_lock:
        _cached_recall_mode = None
        _cached_recall_mode_time = 0.0


# ---------------------------------------------------------------------------
# Auto-enrichment helper (called by run_agent.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tuning constants — centralised with rationale for each threshold.
# These are intentionally *not* in config.yaml: they're implementation
# details that don't benefit from user tuning.
# ---------------------------------------------------------------------------

# Minimum user message length to trigger auto-enrichment.
# "hi", "thanks", "ok" don't benefit from project memory lookup.
_MIN_ENRICH_MESSAGE_LEN = 10

# Minimum brv output length to consider as useful context.
# "No context found" (16 chars) is not useful — discard anything shorter.
_MIN_ENRICH_OUTPUT_LEN = 20

# Tight timeout — auto-enrich blocks the first API call (runs in parallel
# with Honcho prefetch). Must stay well under user-perceptible latency.
_BRV_ENRICH_TIMEOUT = 8  # seconds

# Re-query interval for periodic context refresh (number of user turns).
# Memory changes slowly relative to conversation pace; 10 turns ≈ 5-15 min.
BRV_REFRESH_INTERVAL = 10


def brv_auto_enrich(user_message: str, cwd: str = None) -> Optional[str]:
    """Query ByteRover for context relevant to the user's message.

    Returns context string if available, None otherwise.
    Called by run_agent.py before the first API call per user turn.
    """
    if not check_requirements():
        return None

    if not user_message or not user_message.strip():
        return None

    stripped = user_message.strip()
    if len(stripped) < _MIN_ENRICH_MESSAGE_LEN:
        return None

    # Truncate very long messages to avoid ARG_MAX limits
    if len(stripped) > 5000:
        stripped = stripped[:5000]

    try:
        result = run_brv(["query", "--", stripped], timeout=_BRV_ENRICH_TIMEOUT, cwd=cwd)
        if result["success"] and result["output"]:
            output = result["output"].strip()
            if output and len(output) > _MIN_ENRICH_OUTPUT_LEN:
                logger.info("[brv-enrich] Injected %d chars of context", len(output))
                return output
        return None
    except Exception as e:
        logger.debug("[brv-enrich] Query failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Per-turn auto-curate (called from background thread by run_agent.py)
# ---------------------------------------------------------------------------

# Minimum assistant response length to consider for auto-curation.
# Short responses (greetings, yes/no, clarifying questions) rarely contain
# persistable insights. 200 chars filters out ~95% of trivial exchanges.
_MIN_CURATE_RESPONSE_LEN = 200


def brv_auto_curate_turn(user_message: str, assistant_response: str) -> None:
    """Check if turn has curate-worthy insights via LLM, curate if so.

    Designed to run in a daemon thread — never blocks the main conversation.
    Uses a lightweight LLM call to decide whether the exchange contains
    insights worth persisting, then curates the summary to ByteRover.
    """
    if not check_requirements():
        return
    if len(assistant_response) < _MIN_CURATE_RESPONSE_LEN:
        return

    try:
        from agent.auxiliary_client import call_llm
    except ImportError:
        return

    prompt = (
        "You are a knowledge curator. Review this exchange and decide if it "
        "contains insights worth preserving in long-term memory.\n\n"
        "Worth preserving: architectural decisions, bug fixes with root cause, "
        "user preferences, project patterns, important technical facts.\n"
        "Not worth preserving: greetings, simple Q&A, routine tool output, "
        "debugging steps without resolution, trivial code changes.\n\n"
        f"User: {user_message[:2000]}\n"
        f"Assistant: {assistant_response[:3000]}\n\n"
        "If valuable insights exist, respond with a concise summary (1-3 sentences). "
        "Focus on WHAT was decided/learned and WHY.\n"
        "If nothing notable, respond with exactly: SKIP"
    )

    try:
        resp = call_llm(
            task="brv_curate_check",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300,
            timeout=30.0,
        )
        summary = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.debug("[brv-auto-curate] LLM check failed: %s", e)
        return

    if not summary or summary.upper() == "SKIP":
        return

    try:
        result = run_brv_curate_sync(["curate", "--", summary], timeout=60)
        if result["success"]:
            logger.info("[brv-auto-curate] Curated: %s", summary[:80])
        else:
            logger.debug("[brv-auto-curate] Curate failed: %s", result.get("error", "unknown"))
    except Exception as e:
        logger.debug("[brv-auto-curate] Curate failed: %s", e)


# ---------------------------------------------------------------------------
# Auto-flush helper (called by context_compressor.py)
# ---------------------------------------------------------------------------


def _extract_text(content) -> str:
    """Extract plain text from a message content field (str or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def brv_flush_on_compress(messages: list, compression_count: int) -> None:
    """Extract and curate insights from messages about to be compressed.

    Called by ContextCompressor.compress() before middle turns are discarded.
    Uses an auxiliary LLM to review the conversation and extract valuable
    insights (architectural decisions, bug fixes, patterns) before they are
    lost to context compaction.
    """
    if not check_requirements():
        return

    # Build conversation text from messages about to be compressed
    conversation_parts = []
    for msg in messages:
        role = msg.get("role", "")
        content = _extract_text(msg.get("content", ""))
        if role in ("user", "assistant") and content:
            conversation_parts.append(f"{role.upper()}: {content[:1000]}")

    if not conversation_parts:
        return

    # Truncate to avoid excessive token usage
    full_text = "\n\n".join(conversation_parts)[:8000]

    try:
        from agent.auxiliary_client import call_llm
    except ImportError:
        logger.debug("[brv-flush] auxiliary_client not available, skipping LLM flush")
        return

    prompt = (
        "Review this conversation session for any architectural decisions, bug fixes, "
        "reusable patterns, or important technical facts worth preserving in long-term "
        "memory.\n\n"
        + full_text + "\n\n"
        "If you found valuable insights, provide a concise summary (3-8 bullet points). "
        "Each bullet should capture WHAT was decided/learned and WHY.\n"
        "If nothing notable was found, respond with exactly: SKIP"
    )

    try:
        resp = call_llm(
            task="brv_flush",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=500,
            timeout=45.0,
        )
        summary = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.debug("[brv-flush] LLM call failed: %s", e)
        return

    if not summary or summary.upper() == "SKIP":
        return

    curate_content = f"Session insights (auto-flush #{compression_count + 1}):\n{summary}"
    try:
        result = run_brv_curate_sync(["curate", "--", curate_content], timeout=60)
        if result["success"]:
            logger.info("[brv-flush] LLM-curated insights before compression")
        else:
            logger.warning("[brv-flush] Curate failed: %s", result.get("error", "unknown"))
    except Exception as e:
        logger.debug("[brv-flush] Curate failed: %s", e)
