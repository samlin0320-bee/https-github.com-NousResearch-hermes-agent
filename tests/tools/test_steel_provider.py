# ABOUTME: Live API tests for the Steel cloud browser provider.
# ABOUTME: Requires STEEL_API_KEY env var; skipped when not set.
"""Tests for tools.browser_providers.steel — Steel cloud browser provider.

These tests hit the real Steel API.  They are skipped when ``STEEL_API_KEY``
is not set (e.g. in CI without credentials).
"""

import json
import os
import pytest

_HAS_STEEL_KEY = bool(os.environ.get("STEEL_API_KEY"))
requires_steel = pytest.mark.skipif(
    not _HAS_STEEL_KEY,
    reason="STEEL_API_KEY not set — skipping live Steel tests",
)


# ---------------------------------------------------------------------------
# Unit tests — provider class behaviour (no network needed)
# ---------------------------------------------------------------------------


class TestSteelProviderUnit:
    """Tests that don't require a real API key."""

    def test_provider_name(self):
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        assert provider.provider_name() == "Steel"

    def test_is_configured_without_key(self, monkeypatch):
        monkeypatch.delenv("STEEL_API_KEY", raising=False)
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        assert provider.is_configured() is False

    def test_is_configured_with_key(self, monkeypatch):
        monkeypatch.setenv("STEEL_API_KEY", "test-key-abc")
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        assert provider.is_configured() is True

    def test_base_url_default(self, monkeypatch):
        monkeypatch.delenv("STEEL_BASE_URL", raising=False)
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        assert provider._base_url() == "https://api.steel.dev"

    def test_base_url_override(self, monkeypatch):
        monkeypatch.setenv("STEEL_BASE_URL", "https://custom.steel.example.com/")
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        # Trailing slash should be stripped
        assert provider._base_url() == "https://custom.steel.example.com"

    def test_headers_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("STEEL_API_KEY", raising=False)
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        with pytest.raises(ValueError, match="STEEL_API_KEY"):
            provider._headers()

    def test_headers_with_key(self, monkeypatch):
        monkeypatch.setenv("STEEL_API_KEY", "sk-test-123")
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        headers = provider._headers()
        assert headers["Steel-Api-Key"] == "sk-test-123"
        assert headers["Content-Type"] == "application/json"

    def test_emergency_cleanup_missing_key_returns_silently(self, monkeypatch):
        monkeypatch.delenv("STEEL_API_KEY", raising=False)
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        # Should not raise
        provider.emergency_cleanup("nonexistent-session-id")

    def test_registered_in_provider_registry(self):
        from tools.browser_tool import _PROVIDER_REGISTRY
        from tools.browser_providers.steel import SteelProvider

        assert "steel" in _PROVIDER_REGISTRY
        assert _PROVIDER_REGISTRY["steel"] is SteelProvider


# ---------------------------------------------------------------------------
# Integration tests — real API calls
# ---------------------------------------------------------------------------


@requires_steel
class TestSteelProviderIntegration:
    """Tests that create/release real Steel sessions."""

    def test_create_and_release_session(self):
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        session = provider.create_session("test_integration")

        # Validate returned structure
        assert "session_name" in session
        assert session["session_name"].startswith("hermes_test_integration_")
        assert "bb_session_id" in session
        assert session["bb_session_id"]  # non-empty
        assert "cdp_url" in session
        assert session["cdp_url"].startswith("wss://connect.steel.dev")
        assert "features" in session
        assert session["features"]["steel"] is True

        # Steel returns a live session viewer URL
        if session.get("session_viewer_url"):
            assert "steel.dev" in session["session_viewer_url"] or "http" in session["session_viewer_url"]

        # Release the session
        released = provider.close_session(session["bb_session_id"])
        assert released is True

    def test_close_nonexistent_session_returns_true(self):
        """Closing an already-gone session should not fail."""
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        # Steel requires valid UUID format for session IDs
        result = provider.close_session("00000000-0000-0000-0000-000000000000")
        # 404 is treated as success (session already gone)
        assert result is True

    def test_emergency_cleanup_nonexistent_session(self):
        """Emergency cleanup should not raise even for bad session IDs."""
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        # Should not raise — Steel requires valid UUID format
        provider.emergency_cleanup("00000000-0000-0000-0000-000000000000")


# ---------------------------------------------------------------------------
# Steel scrape tool tests
# ---------------------------------------------------------------------------


class TestSteelScrapeUnit:
    """Tests for the scrape tool that don't need an API key."""

    def test_scrape_returns_error_without_key(self, monkeypatch):
        monkeypatch.delenv("STEEL_API_KEY", raising=False)
        from tools.steel_scrape_tool import steel_scrape

        result = json.loads(steel_scrape("https://example.com"))
        assert "error" in result
        assert "STEEL_API_KEY" in result["error"]

    def test_check_requirements_without_key(self, monkeypatch):
        monkeypatch.delenv("STEEL_API_KEY", raising=False)
        from tools.steel_scrape_tool import check_steel_scrape_requirements

        assert check_steel_scrape_requirements() is False

    def test_check_requirements_with_key(self, monkeypatch):
        monkeypatch.setenv("STEEL_API_KEY", "test-key")
        from tools.steel_scrape_tool import check_steel_scrape_requirements

        assert check_steel_scrape_requirements() is True

    def test_scrape_registered_in_browser_toolset(self):
        import tools.steel_scrape_tool  # noqa: F401 — triggers registry.register()
        from tools.registry import registry

        assert registry.get_schema("steel_scrape") is not None
        assert registry.get_toolset_for_tool("steel_scrape") == "browser"


@requires_steel
class TestSteelScrapeIntegration:
    """Live API tests for Steel scrape."""

    def test_scrape_returns_markdown(self):
        from tools.steel_scrape_tool import steel_scrape

        result = json.loads(steel_scrape("https://example.com", format="markdown"))
        assert "error" not in result
        assert "content" in result
        assert len(result["content"]) > 0

    def test_scrape_returns_metadata(self):
        from tools.steel_scrape_tool import steel_scrape

        result = json.loads(steel_scrape("https://example.com"))
        assert "title" in result or "content" in result


# ---------------------------------------------------------------------------
# End-to-end test — full session lifecycle with CDP URL validation
# ---------------------------------------------------------------------------


@requires_steel
class TestSteelProviderE2E:
    """Full lifecycle test: create session, validate CDP URL format, release."""

    def test_full_lifecycle(self):
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        api_key = os.environ["STEEL_API_KEY"]

        # Create
        session = provider.create_session("e2e_test")
        session_id = session["bb_session_id"]
        cdp_url = session["cdp_url"]

        # Validate CDP URL contains the API key and session ID
        assert f"apiKey={api_key}" in cdp_url
        assert f"sessionId={session_id}" in cdp_url

        # Release
        assert provider.close_session(session_id) is True

        # Double-release should also succeed (404 → True)
        assert provider.close_session(session_id) is True
