"""Test that opencode-go appears in /model list when credentials are set."""

import os
from unittest.mock import patch

from hermes_cli.model_switch import list_authenticated_providers

_FAKE_MODELS_DEV = {
    "opencode-go": {
        "env": ["OPENCODE_GO_API_KEY"],
        "models": ["glm-5", "kimi-k2.5", "mimo-v2-pro", "mimo-v2-omni", "minimax-m2.7", "minimax-m2.5"],
    },
}


@patch("agent.models_dev.fetch_models_dev", return_value=_FAKE_MODELS_DEV)
@patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "test-key"}, clear=False)
def test_opencode_go_appears_when_api_key_set(_mock_fetch):
    """opencode-go should appear in list_authenticated_providers when OPENCODE_GO_API_KEY is set."""
    providers = list_authenticated_providers(current_provider="openrouter")

    opencode_go = next((p for p in providers if p["slug"] == "opencode-go"), None)

    assert opencode_go is not None, "opencode-go should appear when OPENCODE_GO_API_KEY is set"
    assert opencode_go["models"] == ["glm-5", "kimi-k2.5", "mimo-v2-pro", "mimo-v2-omni", "minimax-m2.7", "minimax-m2.5"]
    assert opencode_go["source"] == "built-in"


def test_opencode_go_not_appears_when_no_creds():
    """opencode-go should NOT appear when no credentials are set."""
    # Ensure OPENCODE_GO_API_KEY is not set
    env_without_key = {k: v for k, v in os.environ.items() if k != "OPENCODE_GO_API_KEY"}
    
    with patch.dict(os.environ, env_without_key, clear=True):
        providers = list_authenticated_providers(current_provider="openrouter")
        
        # opencode-go should not be in results
        opencode_go = next((p for p in providers if p["slug"] == "opencode-go"), None)
        assert opencode_go is None, "opencode-go should not appear without credentials"
