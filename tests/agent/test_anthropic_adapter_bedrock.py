"""Tests for Bedrock-specific functionality in the Anthropic adapter."""

import sys
import types

import pytest

from agent.anthropic_adapter import (
    build_anthropic_bedrock_client,
    normalize_model_name,
    build_anthropic_kwargs,
)


@pytest.fixture()
def fake_bedrock_client(monkeypatch):
    """Inject a fake AnthropicBedrock class that captures constructor kwargs."""
    captured = {}

    class FakeBedrock:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AnthropicBedrock = FakeBedrock
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    return captured


# =============================================================================
# build_anthropic_bedrock_client
# =============================================================================

class TestBuildAnthropicBedrockClient:

    def test_import_error_gives_clear_message(self, monkeypatch):
        fake_anthropic = types.ModuleType("anthropic")
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
        with pytest.raises(ImportError, match="anthropic\\[bedrock\\]"):
            build_anthropic_bedrock_client()

    def test_passes_credentials_to_client(self, fake_bedrock_client):
        build_anthropic_bedrock_client(
            aws_access_key="AKID",
            aws_secret_key="SECRET",
            aws_session_token="TOKEN",
            aws_region="eu-west-1",
        )
        assert fake_bedrock_client["aws_access_key"] == "AKID"
        assert fake_bedrock_client["aws_secret_key"] == "SECRET"
        assert fake_bedrock_client["aws_session_token"] == "TOKEN"
        assert fake_bedrock_client["aws_region"] == "eu-west-1"

    def test_omits_keys_when_empty_for_default_chain(self, fake_bedrock_client):
        build_anthropic_bedrock_client(
            aws_access_key="",
            aws_secret_key="",
            aws_region="us-east-1",
        )
        assert "aws_access_key" not in fake_bedrock_client
        assert "aws_secret_key" not in fake_bedrock_client
        assert fake_bedrock_client["aws_region"] == "us-east-1"

    def test_region_defaults_to_us_east_1(self, fake_bedrock_client):
        build_anthropic_bedrock_client(aws_region="")
        assert fake_bedrock_client["aws_region"] == "us-east-1"

    def test_no_beta_headers(self, fake_bedrock_client):
        build_anthropic_bedrock_client()
        assert "default_headers" not in fake_bedrock_client

    def test_construction_error_wraps_in_runtime_error(self, monkeypatch):
        class FailingBedrock:
            def __init__(self, **kwargs):
                raise ValueError("bad creds")

        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.AnthropicBedrock = FailingBedrock
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

        with pytest.raises(RuntimeError, match="Failed to initialize AWS Bedrock client"):
            build_anthropic_bedrock_client()

    def test_has_timeout(self, fake_bedrock_client):
        build_anthropic_bedrock_client()
        assert "timeout" in fake_bedrock_client

    def test_session_token_omitted_when_empty(self, fake_bedrock_client):
        build_anthropic_bedrock_client(
            aws_access_key="AKID",
            aws_secret_key="SECRET",
            aws_session_token="",
        )
        assert "aws_session_token" not in fake_bedrock_client
        assert fake_bedrock_client["aws_access_key"] == "AKID"


# =============================================================================
# normalize_model_name with preserve_model_id
# =============================================================================

class TestNormalizeModelNamePreserve:

    def test_preserve_returns_unchanged(self):
        model = "us.anthropic.claude-sonnet-4-6"
        assert normalize_model_name(model, preserve_model_id=True) == model

    def test_preserve_with_global_prefix(self):
        model = "global.anthropic.claude-opus-4-6-v1"
        assert normalize_model_name(model, preserve_model_id=True) == model

    def test_preserve_with_full_arn(self):
        arn = "arn:aws:bedrock:us-east-1:123456789:inference-profile/us.anthropic.claude-sonnet-4-6"
        assert normalize_model_name(arn, preserve_model_id=True) == arn

    def test_preserve_with_version_suffix(self):
        model = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        assert normalize_model_name(model, preserve_model_id=True) == model

    def test_without_preserve_strips_anthropic_prefix(self):
        assert normalize_model_name("anthropic/claude-opus-4.6") == "claude-opus-4-6"

    def test_without_preserve_converts_dots(self):
        assert normalize_model_name("claude-opus-4.6") == "claude-opus-4-6"

    def test_preserve_keeps_dots(self):
        model = "claude-opus-4.6"
        assert normalize_model_name(model, preserve_model_id=True) == model

    def test_without_preserve_mangles_bedrock_model(self):
        result = normalize_model_name("us.anthropic.claude-sonnet-4-6", preserve_model_id=False)
        assert result != "us.anthropic.claude-sonnet-4-6"
        assert "." not in result


# =============================================================================
# build_anthropic_kwargs with preserve_model_id
# =============================================================================

class TestBuildAnthropicKwargsPreserve:

    def test_preserve_model_id_keeps_bedrock_model_name(self):
        kwargs = build_anthropic_kwargs(
            model="us.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            max_tokens=1024,
            reasoning_config=None,
            preserve_model_id=True,
        )
        assert kwargs["model"] == "us.anthropic.claude-sonnet-4-6"

    def test_without_preserve_normalizes_model(self):
        kwargs = build_anthropic_kwargs(
            model="anthropic/claude-opus-4.6",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            max_tokens=1024,
            reasoning_config=None,
            preserve_model_id=False,
        )
        assert kwargs["model"] == "claude-opus-4-6"

    def test_preserve_with_global_model(self):
        kwargs = build_anthropic_kwargs(
            model="global.anthropic.claude-opus-4-6-v1",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            max_tokens=1024,
            reasoning_config=None,
            preserve_model_id=True,
        )
        assert kwargs["model"] == "global.anthropic.claude-opus-4-6-v1"
