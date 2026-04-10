"""Tests for AWS Bedrock provider support."""

import sys
import types

import pytest

if "dotenv" not in sys.modules:
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = fake_dotenv

from hermes_cli.auth import (
    PROVIDER_REGISTRY,
    resolve_provider,
    resolve_bedrock_credentials,
    get_bedrock_auth_status,
    get_auth_status,
    is_platform_auth_provider,
    AuthError,
)


PROVIDER_ENV_VARS = (
    "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY",
    "KIMI_API_KEY", "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY",
    "AI_GATEWAY_API_KEY", "KILOCODE_API_KEY",
    "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY",
    "OPENCODE_ZEN_API_KEY", "OPENCODE_GO_API_KEY",
    "COPILOT_GITHUB_TOKEN", "NOUS_API_KEY", "GITHUB_TOKEN", "GH_TOKEN",
    "OPENAI_BASE_URL", "HF_TOKEN",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_REGION", "AWS_DEFAULT_REGION",
)


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    for key in PROVIDER_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("hermes_cli.auth._load_auth_store", lambda: {})


# =============================================================================
# Provider Registry
# =============================================================================

class TestBedrockProviderRegistry:

    def test_bedrock_registered(self):
        assert "bedrock" in PROVIDER_REGISTRY
        pconfig = PROVIDER_REGISTRY["bedrock"]
        assert pconfig.name == "AWS Bedrock"
        assert pconfig.auth_type == "aws_credentials"
        assert pconfig.uses_platform_auth is True

    def test_bedrock_has_no_api_key_env_vars(self):
        pconfig = PROVIDER_REGISTRY["bedrock"]
        assert pconfig.api_key_env_vars == ()

    def test_bedrock_has_no_base_url_env_var(self):
        pconfig = PROVIDER_REGISTRY["bedrock"]
        assert pconfig.base_url_env_var == ""

    def test_bedrock_has_empty_inference_base_url(self):
        pconfig = PROVIDER_REGISTRY["bedrock"]
        assert pconfig.inference_base_url == ""


# =============================================================================
# is_platform_auth_provider
# =============================================================================

class TestIsPlatformAuthProvider:

    def test_bedrock_is_platform_auth(self):
        assert is_platform_auth_provider("bedrock") is True

    def test_anthropic_is_not_platform_auth(self):
        assert is_platform_auth_provider("anthropic") is False

    def test_openrouter_is_not_platform_auth(self):
        assert is_platform_auth_provider("openrouter") is False

    def test_unknown_provider_is_not_platform_auth(self):
        assert is_platform_auth_provider("nonexistent-xyz") is False

    def test_api_key_providers_are_not_platform_auth(self):
        for pid, pconfig in PROVIDER_REGISTRY.items():
            if pconfig.auth_type == "api_key":
                assert is_platform_auth_provider(pid) is False, (
                    f"API-key provider {pid!r} should not be platform auth"
                )


# =============================================================================
# Provider Resolution
# =============================================================================

class TestBedrockResolveProvider:

    def test_explicit_bedrock(self):
        assert resolve_provider("bedrock") == "bedrock"

    def test_alias_aws(self):
        assert resolve_provider("aws") == "bedrock"

    def test_alias_aws_bedrock(self):
        assert resolve_provider("aws-bedrock") == "bedrock"

    def test_alias_amazon_bedrock(self):
        assert resolve_provider("amazon-bedrock") == "bedrock"

    def test_alias_case_insensitive(self):
        assert resolve_provider("AWS") == "bedrock"
        assert resolve_provider("Aws-Bedrock") == "bedrock"

    def test_auto_detects_aws_credentials(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        assert resolve_provider("auto") == "bedrock"

    def test_auto_requires_both_aws_keys_access_only(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
        with pytest.raises(AuthError, match="No inference provider"):
            resolve_provider("auto")

    def test_auto_requires_both_aws_keys_secret_only(self, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        with pytest.raises(AuthError, match="No inference provider"):
            resolve_provider("auto")

    def test_openrouter_takes_priority_over_bedrock(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        assert resolve_provider("auto") == "openrouter"

    def test_api_key_provider_takes_priority_over_bedrock(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "kimi-key")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        assert resolve_provider("auto") == "kimi-coding"


# =============================================================================
# Bedrock Credential Resolution
# =============================================================================

class TestResolveBedrockCredentials:

    def test_with_env_vars(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRET")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "TOKEN")
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        creds = resolve_bedrock_credentials()
        assert creds["provider"] == "bedrock"
        assert creds["aws_access_key"] == "AKID"
        assert creds["aws_secret_key"] == "SECRET"
        assert creds["aws_session_token"] == "TOKEN"
        assert creds["aws_region"] == "eu-west-1"
        assert creds["source"] == "env"

    def test_without_env_vars(self):
        creds = resolve_bedrock_credentials()
        assert creds["provider"] == "bedrock"
        assert creds["aws_access_key"] == ""
        assert creds["aws_secret_key"] == ""
        assert creds["aws_session_token"] == ""
        assert creds["source"] == "aws_default_chain"

    def test_region_fallback_aws_region(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "ap-southeast-1")
        creds = resolve_bedrock_credentials()
        assert creds["aws_region"] == "ap-southeast-1"

    def test_region_fallback_aws_default_region(self, monkeypatch):
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
        creds = resolve_bedrock_credentials()
        assert creds["aws_region"] == "us-west-2"

    def test_region_aws_region_takes_priority(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "eu-central-1")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
        creds = resolve_bedrock_credentials()
        assert creds["aws_region"] == "eu-central-1"

    def test_region_defaults_to_us_east_1(self):
        creds = resolve_bedrock_credentials()
        assert creds["aws_region"] == "us-east-1"

    def test_session_token_optional(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRET")
        creds = resolve_bedrock_credentials()
        assert creds["aws_session_token"] == ""
        assert creds["source"] == "env"

    def test_source_env_only_when_both_keys_present(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
        creds = resolve_bedrock_credentials()
        assert creds["source"] == "aws_default_chain"


# =============================================================================
# Bedrock Auth Status
# =============================================================================

class TestGetBedrockAuthStatus:

    def test_configured_with_env_vars(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRET")
        status = get_bedrock_auth_status()
        assert status["configured"] is True
        assert status["logged_in"] is True
        assert status["provider"] == "bedrock"
        assert status["name"] == "AWS Bedrock"
        assert status["key_source"] == "AWS_ACCESS_KEY_ID"

    def test_unconfigured_without_env_vars(self):
        status = get_bedrock_auth_status()
        assert status["configured"] is False
        assert status["logged_in"] is False
        assert status["key_source"] == ""

    def test_shows_region(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        status = get_bedrock_auth_status()
        assert status["aws_region"] == "eu-west-1"

    def test_region_defaults_to_us_east_1(self):
        status = get_bedrock_auth_status()
        assert status["aws_region"] == "us-east-1"

    def test_get_auth_status_dispatches_to_bedrock(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRET")
        status = get_auth_status("bedrock")
        assert status["configured"] is True
        assert status["provider"] == "bedrock"


# =============================================================================
# Runtime Provider Resolution
# =============================================================================

class TestBedrockRuntimeProvider:

    def test_runtime_bedrock_explicit(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRET")
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        from hermes_cli.runtime_provider import resolve_runtime_provider
        result = resolve_runtime_provider(requested="bedrock")
        assert result["provider"] == "bedrock"
        assert result["api_mode"] == "bedrock_converse"
        assert result["uses_platform_auth"] is True
        assert result["api_key"] == "bedrock-sigv4-auth"
        assert result["base_url"] == ""
        assert result["requested_provider"] == "bedrock"

    def test_runtime_bedrock_has_platform_credentials(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRET")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "TOKEN")
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        from hermes_cli.runtime_provider import resolve_runtime_provider
        result = resolve_runtime_provider(requested="bedrock")
        pc = result["platform_credentials"]
        assert pc["provider"] == "bedrock"
        assert pc["aws_access_key"] == "AKID"
        assert pc["aws_secret_key"] == "SECRET"
        assert pc["aws_session_token"] == "TOKEN"
        assert pc["aws_region"] == "eu-west-1"

    def test_runtime_bedrock_auto_detected(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRET")
        from hermes_cli.runtime_provider import resolve_runtime_provider
        result = resolve_runtime_provider(requested="auto")
        assert result["provider"] == "bedrock"
        assert result["uses_platform_auth"] is True
        assert result["requested_provider"] == "auto"

    def test_runtime_bedrock_default_chain(self, monkeypatch):
        from hermes_cli.runtime_provider import resolve_runtime_provider
        result = resolve_runtime_provider(requested="bedrock")
        pc = result["platform_credentials"]
        assert pc["source"] == "aws_default_chain"

    def test_runtime_bedrock_via_alias(self, monkeypatch):
        from hermes_cli.runtime_provider import resolve_runtime_provider
        result = resolve_runtime_provider(requested="aws")
        assert result["provider"] == "bedrock"
        assert result["requested_provider"] == "aws"

    def test_platform_credentials_matches_resolve_bedrock_credentials(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRET")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "TOKEN")
        monkeypatch.setenv("AWS_REGION", "ap-northeast-1")
        from hermes_cli.runtime_provider import resolve_runtime_provider
        result = resolve_runtime_provider(requested="bedrock")
        pc = result["platform_credentials"]
        direct = resolve_bedrock_credentials()
        assert pc == direct


# =============================================================================
# Model Catalog
# =============================================================================

class TestBedrockModelCatalog:

    def test_provider_models_has_bedrock(self):
        from hermes_cli.models import _PROVIDER_MODELS
        assert "bedrock" in _PROVIDER_MODELS
        models = _PROVIDER_MODELS["bedrock"]
        assert len(models) >= 5

    def test_provider_label(self):
        from hermes_cli.models import _PROVIDER_LABELS
        assert _PROVIDER_LABELS["bedrock"] == "AWS Bedrock"

    def test_provider_aliases(self):
        from hermes_cli.models import _PROVIDER_ALIASES
        assert _PROVIDER_ALIASES["aws"] == "bedrock"
        assert _PROVIDER_ALIASES["aws-bedrock"] == "bedrock"
        assert _PROVIDER_ALIASES["amazon-bedrock"] == "bedrock"

    def test_bedrock_models_use_regional_prefix(self):
        from hermes_cli.models import _PROVIDER_MODELS
        valid_prefixes = ("us.", "global.")
        for model in _PROVIDER_MODELS["bedrock"]:
            assert model.startswith(valid_prefixes), (
                f"Bedrock model {model!r} should start with us. or global."
            )

    def test_bedrock_models_include_us_and_global_variants(self):
        from hermes_cli.models import _PROVIDER_MODELS
        models = _PROVIDER_MODELS["bedrock"]
        us_models = [m for m in models if m.startswith("us.")]
        global_models = [m for m in models if m.startswith("global.")]
        assert len(us_models) >= 1, "Expected at least one us.* model"
        assert len(global_models) >= 1, "Expected at least one global.* model"
