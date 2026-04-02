"""ByteRover onboarding — 2-step setup flow with deterministic parsing.

Step 1: Provider selection (ByteRover free / OpenRouter / Anthropic / Skip)
Step 2: Storage choice (local / ByteRover Cloud)

The LLM presents formatted choices; two tool handlers (setup-provider,
setup-storage) do all parsing and execution deterministically.
"""

import re
from pathlib import Path
from typing import Optional

from byterover_integration.client import check_requirements, get_hermes_home, get_brv_default_cwd


# ---------------------------------------------------------------------------
# Onboarding marker (keep existing pattern)
# ---------------------------------------------------------------------------

def _get_onboarded_marker() -> Path:
    """Return the path to the ByteRover onboarding marker file."""
    return get_hermes_home() / ".byterover-onboarded"


def is_brv_onboarded() -> bool:
    """Check if ByteRover onboarding has been completed."""
    return _get_onboarded_marker().exists()


def mark_brv_onboarded() -> None:
    """Mark ByteRover onboarding as completed."""
    marker = _get_onboarded_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def _display_brv_cwd() -> str:
    """Return a display-safe path for the brv working directory (uses ~ for home dir)."""
    brv_cwd = get_brv_default_cwd()
    try:
        return "~/" + str(brv_cwd.relative_to(Path.home()))
    except ValueError:
        return str(brv_cwd)


# ---------------------------------------------------------------------------
# Setup step state (file-based: ~/.hermes/byterover/.setup-step)
# ---------------------------------------------------------------------------

def _get_setup_step_path() -> Path:
    """Return path to the setup step tracker file."""
    return get_brv_default_cwd() / ".setup-step"


def get_setup_step() -> Optional[str]:
    """Return current setup step ('provider', 'storage') or None if complete."""
    path = _get_setup_step_path()
    if not path.exists():
        return None
    step = path.read_text(encoding="utf-8").strip()
    return step if step in ("provider", "storage") else None


_VALID_SETUP_STEPS = ("provider", "storage")


def set_setup_step(step: str) -> None:
    """Write current setup step to state file."""
    if step not in _VALID_SETUP_STEPS:
        raise ValueError(f"Invalid setup step: {step!r}. Must be one of {_VALID_SETUP_STEPS}")
    path = _get_setup_step_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(step, encoding="utf-8")


def clear_setup_step() -> None:
    """Remove the setup step file (onboarding complete)."""
    path = _get_setup_step_path()
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Compact onboarding prompts (~100 tokens each)
# ---------------------------------------------------------------------------

PROVIDER_PROMPT = (
    "## ByteRover Setup (Step 1/2)\n\n"
    "ByteRover long-term memory needs a provider to power its AI. "
    "Present these choices to the user:\n\n"
    "1. ByteRover (free, no key needed)\n"
    "2. OpenRouter (paste API key after number)\n"
    "3. Anthropic (paste API key after number)\n"
    "4. Skip for now\n\n"
    "IMPORTANT: Always include text in your response.\n"
    "When the user replies, run the appropriate brv command via terminal:\n"
    "- Choice 1 (ByteRover free): brv providers connect byterover\n"
    "- Choice 2 (OpenRouter): BRV_API_KEY=<key> brv providers connect openrouter\n"
    "- Choice 3 (Anthropic): BRV_API_KEY=<key> brv providers connect anthropic\n"
    "- Choice 4 (Skip): No command needed, move on.\n"
    "SECURITY: Always pass API keys via the BRV_API_KEY env var prefix, never as --api-key CLI args.\n"
    "After connecting, proceed to Step 2 (storage choice)."
)

STORAGE_PROMPT = (
    "## ByteRover Setup (Step 2/2)\n\n"
    "Provider connected! Ask the user where to store their memory:\n\n"
    "🔒 **Local only** (default) — private, works offline, fully functional\n"
    "☁️ **ByteRover Cloud** (free tier available) — sync across devices, "
    "share with your team, browse/edit in the dashboard, automatic backup\n"
    "   → Get your key at app.byterover.dev/settings/keys\n\n"
    "Reply \"local\" or paste your ByteRover cloud key.\n\n"
    "IMPORTANT: Always include text in your response.\n"
    "When the user replies, run via terminal:\n"
    "- Local: No command needed, just confirm.\n"
    "- Cloud: BRV_API_KEY=<key> brv login && brv space list && brv pull\n"
    "SECURITY: Always pass API keys via the BRV_API_KEY env var prefix, never as --api-key CLI args.\n"
    "After setup, tell the user they can say 'remember that...' anytime."
)


def get_brv_onboarding_prompt() -> Optional[str]:
    """Return a short onboarding prompt for the current step, or None if done.

    Returns None if already onboarded or brv is not available.
    """
    if is_brv_onboarded():
        return None
    if not check_requirements():
        return None

    step = get_setup_step()
    if step is None:
        # First call — initialize to step 1
        set_setup_step("provider")
        step = "provider"

    if step == "provider":
        return PROVIDER_PROMPT
    if step == "storage":
        return STORAGE_PROMPT
    return None


# ---------------------------------------------------------------------------
# Deterministic parsers for user replies
# ---------------------------------------------------------------------------

_API_KEY_PATTERN = re.compile(r'(sk-[a-zA-Z0-9_\-]{20,})')
# ByteRover-native cloud keys: mixed-case alphanumeric, 30-60 chars.
# Requires both uppercase and lowercase letters to avoid matching hex hashes,
# UUIDs, or other tokens. Only used in parse_storage_choice (not provider).
_BRV_CLOUD_KEY_PATTERN = re.compile(r'\b((?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])[A-Za-z0-9]{30,60})\b')


def _extract_api_key(text: str) -> Optional[str]:
    """Extract an API-key-like token (sk-xxx) from text."""
    match = _API_KEY_PATTERN.search(text)
    return match.group(1) if match else None


def _extract_brv_cloud_key(text: str) -> Optional[str]:
    """Extract a ByteRover-native cloud key (alphanumeric, 30-60 chars)."""
    match = _BRV_CLOUD_KEY_PATTERN.search(text)
    return match.group(1) if match else None


def _guess_provider_from_key(key: str) -> Optional[str]:
    """Guess provider from API key prefix."""
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("sk-ant-"):
        return "anthropic"
    return None


def parse_provider_choice(raw_reply: str) -> dict:
    """Parse user reply for provider step.

    Returns dict with keys:
      action: "skip" | "connect" | "need_key" | "unclear"
      provider: str (when action is connect or need_key)
      args: list (brv CLI args when action is connect)
    """
    reply = raw_reply.strip()
    reply_lower = reply.lower()

    # Skip
    if any(kw in reply_lower for kw in ("skip", "later", "none")) or reply_lower == "4":
        return {"action": "skip"}

    # ByteRover (free, no key)
    if reply_lower in ("1", "byterover", "free"):
        return {
            "action": "connect",
            "provider": "byterover",
            "args": ["providers", "connect", "byterover"],
        }

    # OpenRouter
    if reply_lower == "2" or reply_lower.startswith("2 ") or "openrouter" in reply_lower:
        key = _extract_api_key(reply)
        if key:
            return {
                "action": "connect",
                "provider": "openrouter",
                "args": ["providers", "connect", "openrouter"],
                "env": {"BRV_API_KEY": key},
            }
        return {"action": "need_key", "provider": "openrouter"}

    # Anthropic
    if reply_lower == "3" or reply_lower.startswith("3 ") or "anthropic" in reply_lower:
        key = _extract_api_key(reply)
        if key:
            return {
                "action": "connect",
                "provider": "anthropic",
                "args": ["providers", "connect", "anthropic"],
                "env": {"BRV_API_KEY": key},
            }
        return {"action": "need_key", "provider": "anthropic"}

    # Fallback: detect API key anywhere and guess provider
    key = _extract_api_key(reply)
    if key:
        provider = _guess_provider_from_key(key)
        if provider:
            return {
                "action": "connect",
                "provider": provider,
                "args": ["providers", "connect", provider],
                "env": {"BRV_API_KEY": key},
            }

    return {"action": "unclear"}


def parse_storage_choice(raw_reply: str) -> dict:
    """Parse user reply for storage step.

    Returns dict with keys:
      action: "local" | "cloud" | "need_cloud_key" | "unclear"
      api_key: str (when action is cloud)
    """
    reply = raw_reply.strip()
    reply_lower = reply.lower()

    # Local
    if any(kw in reply_lower for kw in ("local", "disk", "offline", "no cloud", "skip", "later")):
        return {"action": "local"}
    if reply_lower == "1":
        return {"action": "local"}

    # Cloud with key (try sk-* pattern first, then ByteRover-native key)
    key = _extract_api_key(reply) or _extract_brv_cloud_key(reply)
    if key:
        return {"action": "cloud", "api_key": key}

    # Cloud without key
    if any(kw in reply_lower for kw in ("cloud", "sync", "team", "remote")):
        return {"action": "need_cloud_key"}
    if reply_lower == "2":
        return {"action": "need_cloud_key"}

    return {"action": "unclear"}
