"""Tests for ByteRover integration — auto-enrich, flush, onboarding, bridge."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from byterover_integration.client import check_requirements
from byterover_integration.recall import (
    brv_auto_enrich,
    brv_flush_on_compress,
    get_brv_recall_mode,
)
from byterover_integration.onboarding import (
    is_brv_onboarded,
    mark_brv_onboarded,
    get_brv_onboarding_prompt,
)
from byterover_integration.client import (
    _resolve_brv_path,
    _reset_cache,
    run_brv,
    run_brv_curate_sync,
    _log_brv_operation,
    _reset_brv_file_logger,
)


# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture(autouse=True)
def reset_brv_cache():
    """Reset cached brv path before each test."""
    _reset_cache()
    yield
    _reset_cache()


@pytest.fixture()
def fake_brv_home(tmp_path, monkeypatch):
    """Set HERMES_HOME to temp dir for onboarding marker and recall mode tests."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Patch the single source of truth — get_hermes_home() in client.py.
    # All submodules (recall, onboarding) call this function, so one patch
    # is sufficient for full test isolation.
    import byterover_integration.client as br_client
    monkeypatch.setattr(br_client, "get_hermes_home", lambda: tmp_path)
    # Reset recall mode cache so tests read from the patched HERMES_HOME
    import byterover_integration.recall as br_recall
    br_recall._reset_recall_mode_cache()
    yield tmp_path
    br_recall._reset_recall_mode_cache()


# =========================================================================
# check_requirements / _resolve_brv_path
# =========================================================================

class TestCheckRequirements:
    def test_returns_false_when_brv_not_found(self):
        with patch("shutil.which", return_value=None):
            assert check_requirements() is False

    def test_returns_true_when_brv_found(self):
        with patch("shutil.which", return_value="/usr/local/bin/brv"):
            assert check_requirements() is True

    def test_caches_negative_result(self):
        # Also patch Path.exists so fallback candidates don't match on
        # machines where brv happens to be installed at a well-known path.
        with patch("shutil.which", return_value=None) as mock_which, \
             patch.object(Path, "exists", return_value=False):
            assert check_requirements() is False
            assert check_requirements() is False
            # First call tries which, then fallback candidates. Second returns cached.
            assert mock_which.call_count == 1

    def test_caches_positive_result(self):
        with patch("shutil.which", return_value="/usr/bin/brv") as mock_which:
            assert check_requirements() is True
            assert check_requirements() is True
            assert mock_which.call_count == 1


# =========================================================================
# run_brv
# =========================================================================

class TestRunBrv:
    def test_returns_error_when_brv_not_found(self):
        with patch("shutil.which", return_value=None):
            result = run_brv(["query", "test"])
            assert result["success"] is False
            assert "not found" in result["error"]

    def test_successful_command(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "some context output"
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run", return_value=mock_result):
            result = run_brv(["query", "test"])
            assert result["success"] is True
            assert result["output"] == "some context output"

    def test_failed_command(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "No provider connected"

        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run", return_value=mock_result):
            result = run_brv(["query", "test"])
            assert result["success"] is False
            assert "No provider connected" in result["error"]

    def test_timeout_handling(self):
        import subprocess
        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="brv", timeout=120)):
            result = run_brv(["query", "test"])
            assert result["success"] is False
            assert "timed out" in result["error"]


# =========================================================================
# brv_auto_enrich
# =========================================================================

class TestBrvAutoEnrich:
    def test_returns_none_when_brv_not_available(self):
        with patch("shutil.which", return_value=None):
            assert brv_auto_enrich("test query") is None

    def test_returns_none_for_empty_message(self):
        with patch("shutil.which", return_value="/usr/bin/brv"):
            assert brv_auto_enrich("") is None
            assert brv_auto_enrich("   ") is None

    def test_returns_none_for_short_message(self):
        with patch("shutil.which", return_value="/usr/bin/brv"):
            assert brv_auto_enrich("hi") is None
            assert brv_auto_enrich("thanks") is None

    def test_returns_context_on_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Auth uses JWT tokens with 24h expiry. Stored in httpOnly cookies."
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run", return_value=mock_result):
            result = brv_auto_enrich("How does authentication work in this project?")
            assert result is not None
            assert "JWT" in result

    def test_returns_none_on_trivial_output(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "No context found"
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run", return_value=mock_result):
            # "No context found" is only 16 chars, below 20 threshold
            result = brv_auto_enrich("Tell me about the project")
            assert result is None

    def test_handles_subprocess_error(self):
        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run", side_effect=Exception("connection refused")):
            assert brv_auto_enrich("test query with enough chars") is None


# =========================================================================
# brv_flush_on_compress
# =========================================================================

class TestBrvFlushOnCompress:
    def test_noop_when_brv_not_available(self):
        with patch("shutil.which", return_value=None):
            # Should not raise
            brv_flush_on_compress([], 0)

    def test_noop_with_empty_messages(self):
        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run") as mock_run:
            brv_flush_on_compress([], 0)
            mock_run.assert_not_called()

    def test_extracts_insights_from_assistant_messages(self):
        messages = [
            {"role": "user", "content": "How should I structure the auth module?"},
            {"role": "assistant", "content": "Based on the codebase analysis, I recommend using JWT tokens with httpOnly cookies for session management. The middleware pattern in authMiddleware.ts provides a clean separation of concerns."},
        ]

        mock_llm_resp = MagicMock()
        mock_llm_resp.choices = [MagicMock()]
        mock_llm_resp.choices[0].message.content = "Use JWT with httpOnly cookies for auth."

        with patch("byterover_integration.recall.check_requirements", return_value=True), \
             patch("agent.auxiliary_client.call_llm", return_value=mock_llm_resp), \
             patch("byterover_integration.recall.run_brv_curate_sync",
                   return_value={"success": True, "output": "Curated"}) as mock_sync:
            brv_flush_on_compress(messages, 0)
            assert mock_sync.called
            call_args = mock_sync.call_args[0][0]
            assert call_args[0] == "curate"

    def test_skips_short_assistant_messages(self):
        messages = [
            {"role": "assistant", "content": "OK, done."},
        ]

        with patch("byterover_integration.recall.run_brv_curate_sync") as mock_sync:
            brv_flush_on_compress(messages, 0)
            mock_sync.assert_not_called()

    def test_llm_summary_flows_to_curate(self):
        """Verify that the LLM-generated summary is passed through to curate."""
        messages = [
            {"role": "user", "content": "How should I handle database migrations?"},
            {"role": "assistant", "content": (
                "Sure, I'd be happy to help with that! Let me walk you through it.\n"
                "Here are some important considerations for your project.\n"
                "In summary, always use versioned migrations with rollback support and test them in staging first."
            )},
        ]

        mock_llm_resp = MagicMock()
        mock_llm_resp.choices = [MagicMock()]
        mock_llm_resp.choices[0].message.content = "Always use versioned migrations with rollback support."

        with patch("byterover_integration.recall.check_requirements", return_value=True), \
             patch("agent.auxiliary_client.call_llm", return_value=mock_llm_resp), \
             patch("byterover_integration.recall.run_brv_curate_sync",
                   return_value={"success": True, "output": "Curated"}) as mock_sync:
            brv_flush_on_compress(messages, 0)
            assert mock_sync.called
            curate_content = mock_sync.call_args[0][0][1]  # args[1] = the summary string
            assert "versioned migrations" in curate_content


# =========================================================================
# Onboarding
# =========================================================================

class TestOnboarding:
    def test_not_onboarded_initially(self, fake_brv_home):
        assert is_brv_onboarded() is False

    def test_mark_onboarded(self, fake_brv_home):
        mark_brv_onboarded()
        assert is_brv_onboarded() is True

    def test_onboarding_prompt_step1(self, fake_brv_home):
        with patch("shutil.which", return_value="/usr/bin/brv"):
            prompt = get_brv_onboarding_prompt()
            assert prompt is not None
            assert "Step 1" in prompt
            assert "brv providers connect" in prompt

    def test_onboarding_prompt_step2(self, fake_brv_home):
        from byterover_integration.onboarding import set_setup_step
        set_setup_step("storage")
        with patch("shutil.which", return_value="/usr/bin/brv"):
            prompt = get_brv_onboarding_prompt()
            assert prompt is not None
            assert "Step 2" in prompt
            assert "brv login" in prompt
            assert "Cloud" in prompt

    def test_no_onboarding_prompt_after_onboarded(self, fake_brv_home):
        mark_brv_onboarded()
        with patch("shutil.which", return_value="/usr/bin/brv"):
            assert get_brv_onboarding_prompt() is None

    def test_no_onboarding_prompt_when_brv_not_installed(self, fake_brv_home):
        with patch("shutil.which", return_value=None):
            assert get_brv_onboarding_prompt() is None

    def test_setup_step_tracking(self, fake_brv_home):
        from byterover_integration.onboarding import get_setup_step, set_setup_step, clear_setup_step
        assert get_setup_step() is None
        set_setup_step("provider")
        assert get_setup_step() == "provider"
        set_setup_step("storage")
        assert get_setup_step() == "storage"
        clear_setup_step()
        assert get_setup_step() is None


class TestParseProviderChoice:
    def test_skip_by_number(self):
        from byterover_integration.onboarding import parse_provider_choice
        assert parse_provider_choice("4")["action"] == "skip"

    def test_skip_by_word(self):
        from byterover_integration.onboarding import parse_provider_choice
        assert parse_provider_choice("skip")["action"] == "skip"

    def test_byterover_by_number(self):
        from byterover_integration.onboarding import parse_provider_choice
        choice = parse_provider_choice("1")
        assert choice["action"] == "connect"
        assert choice["provider"] == "byterover"

    def test_openrouter_with_key(self):
        from byterover_integration.onboarding import parse_provider_choice
        choice = parse_provider_choice("2 sk-or-v1-abc123def456ghi789jkl")
        assert choice["action"] == "connect"
        assert choice["provider"] == "openrouter"
        # Key passed via env, not CLI args (avoids /proc exposure)
        assert "--api-key" not in choice["args"]
        assert choice["env"]["BRV_API_KEY"] == "sk-or-v1-abc123def456ghi789jkl"

    def test_anthropic_without_key(self):
        from byterover_integration.onboarding import parse_provider_choice
        choice = parse_provider_choice("3")
        assert choice["action"] == "need_key"
        assert choice["provider"] == "anthropic"

    def test_unclear_reply(self):
        from byterover_integration.onboarding import parse_provider_choice
        assert parse_provider_choice("what?")["action"] == "unclear"

    def test_bare_key_auto_detects_provider(self):
        from byterover_integration.onboarding import parse_provider_choice
        choice = parse_provider_choice("sk-or-v1-abc123def456ghi789jkl")
        assert choice["action"] == "connect"
        assert choice["provider"] == "openrouter"

    def test_byterover_by_name(self):
        from byterover_integration.onboarding import parse_provider_choice
        choice = parse_provider_choice("byterover")
        assert choice["action"] == "connect"
        assert choice["provider"] == "byterover"


class TestParseStorageChoiceBrvKeys:
    """Test parse_storage_choice with ByteRover-native cloud keys."""

    def test_brv_native_key_detected_as_cloud(self):
        from byterover_integration.onboarding import parse_storage_choice
        choice = parse_storage_choice("Ri1Wpg2FaC4tz54ApgWj1QmnaK3ef1AIn6xUwzGgP10")
        assert choice["action"] == "cloud"
        assert choice["api_key"] == "Ri1Wpg2FaC4tz54ApgWj1QmnaK3ef1AIn6xUwzGgP10"

    def test_sk_key_still_works(self):
        from byterover_integration.onboarding import parse_storage_choice
        choice = parse_storage_choice("sk-or-v1-fakekey12345678901234")
        assert choice["action"] == "cloud"
        assert choice["api_key"] == "sk-or-v1-fakekey12345678901234"

    def test_short_string_not_detected_as_key(self):
        from byterover_integration.onboarding import parse_storage_choice
        choice = parse_storage_choice("abc123")
        assert choice["action"] == "unclear"

    def test_brv_native_key_with_prefix_text(self):
        from byterover_integration.onboarding import parse_storage_choice
        choice = parse_storage_choice(
            "Byterover API KEY: Ri1Wpg2FaC4tz54ApgWj1QmnaK3ef1AIn6xUwzGgP10"
        )
        assert choice["action"] == "cloud"
        assert choice["api_key"] == "Ri1Wpg2FaC4tz54ApgWj1QmnaK3ef1AIn6xUwzGgP10"


# =========================================================================
# run_agent.py helper: _inject_memory_context
# =========================================================================

class TestInjectMemoryContext:
    """Test _inject_memory_context utility.

    ByteRover context is now baked into the system prompt at session start,
    so per-turn injection only contains Honcho contexts.
    """

    def _contexts(self, ctx, label="Honcho conversational memory"):
        """Helper: build contexts list with a single source."""
        return [(label, ctx)]

    def test_string_content(self):
        from run_agent import _inject_memory_context
        result = _inject_memory_context("user question", self._contexts("honcho data"))
        assert "user question" in result
        assert "honcho data" in result

    def test_list_content(self):
        from run_agent import _inject_memory_context
        content = [{"type": "text", "text": "user question"}]
        result = _inject_memory_context(content, self._contexts("honcho data"))
        assert isinstance(result, list)
        assert len(result) == 2
        assert "honcho data" in result[1]["text"]
        assert "<memory-context>" in result[1]["text"]
        assert "</memory-context>" in result[1]["text"]

    def test_empty_content(self):
        from run_agent import _inject_memory_context
        result = _inject_memory_context("", self._contexts("honcho data"))
        assert "honcho data" in result

    def test_no_active_contexts(self):
        from run_agent import _inject_memory_context
        result = _inject_memory_context("user question", self._contexts(""))
        assert result == "user question"

    def test_none_content_with_context(self):
        from run_agent import _inject_memory_context
        result = _inject_memory_context(None, self._contexts("honcho data"))
        assert isinstance(result, str)
        assert "honcho data" in result

    def test_none_content_with_no_active_contexts(self):
        from run_agent import _inject_memory_context
        result = _inject_memory_context(None, self._contexts(""))
        assert result is None

    def test_multiple_sources_combined(self):
        from run_agent import _inject_memory_context
        contexts = [
            ("Honcho conversational memory", "honcho data"),
            ("Custom memory source", "custom data"),
        ]
        result = _inject_memory_context("user question", contexts)
        assert "honcho data" in result
        assert "custom data" in result
        # Single [System note] block, not two
        assert result.count("[System note:") == 1
        # Wrapped in prompt injection fence
        assert "<memory-context>" in result
        assert "</memory-context>" in result


# =========================================================================
# get_brv_recall_mode
# =========================================================================

class TestRecallMode:
    def test_defaults_to_hybrid_when_no_config(self, fake_brv_home):
        assert get_brv_recall_mode() == "hybrid"

    def test_reads_hybrid_mode(self, fake_brv_home):
        config_path = fake_brv_home / "config.yaml"
        config_path.write_text("byterover:\n  recall_mode: hybrid\n")
        assert get_brv_recall_mode() == "hybrid"

    def test_reads_context_mode(self, fake_brv_home):
        config_path = fake_brv_home / "config.yaml"
        config_path.write_text("byterover:\n  recall_mode: context\n")
        assert get_brv_recall_mode() == "context"

    def test_reads_tools_mode(self, fake_brv_home):
        config_path = fake_brv_home / "config.yaml"
        config_path.write_text("byterover:\n  recall_mode: tools\n")
        assert get_brv_recall_mode() == "tools"

    def test_reads_off_mode(self, fake_brv_home):
        config_path = fake_brv_home / "config.yaml"
        config_path.write_text("byterover:\n  recall_mode: off\n")
        assert get_brv_recall_mode() == "off"

    def test_invalid_mode_defaults_to_hybrid(self, fake_brv_home):
        config_path = fake_brv_home / "config.yaml"
        config_path.write_text("byterover:\n  recall_mode: invalid_mode\n")
        assert get_brv_recall_mode() == "hybrid"

    def test_missing_section_defaults_to_hybrid(self, fake_brv_home):
        config_path = fake_brv_home / "config.yaml"
        config_path.write_text("model: gemini/gemini-2.5-flash\n")
        assert get_brv_recall_mode() == "hybrid"


# =========================================================================
# Auto-enrich gating by recall_mode
# =========================================================================

class TestAutoEnrichGating:
    """Verify that auto-enrichment respects recall_mode."""

    def test_skip_when_mode_tools(self, fake_brv_home):
        """In 'tools' mode, auto-enrich should NOT run."""
        config_path = fake_brv_home / "config.yaml"
        config_path.write_text("byterover:\n  recall_mode: tools\n")
        mode = get_brv_recall_mode()
        assert mode == "tools"
        # The gate in run_agent.py: _brv_mode in ("hybrid", "context")
        assert mode not in ("hybrid", "context")

    def test_skip_when_mode_off(self, fake_brv_home):
        """In 'off' mode, auto-enrich should NOT run."""
        config_path = fake_brv_home / "config.yaml"
        config_path.write_text("byterover:\n  recall_mode: off\n")
        mode = get_brv_recall_mode()
        assert mode == "off"
        assert mode not in ("hybrid", "context")

    def test_runs_when_mode_hybrid(self, fake_brv_home):
        """In 'hybrid' mode, auto-enrich should run."""
        config_path = fake_brv_home / "config.yaml"
        config_path.write_text("byterover:\n  recall_mode: hybrid\n")
        mode = get_brv_recall_mode()
        assert mode in ("hybrid", "context")

    def test_runs_when_mode_context(self, fake_brv_home):
        """In 'context' mode, auto-enrich should run."""
        config_path = fake_brv_home / "config.yaml"
        config_path.write_text("byterover:\n  recall_mode: context\n")
        mode = get_brv_recall_mode()
        assert mode in ("hybrid", "context")


# =========================================================================
# ByteRover memory bridge helpers
# =========================================================================

class TestMemoryBridge:
    """Tests for build_brv_memory_content and brv_curate_fire_and_forget."""

    def test_brv_memory_content_add_user(self):
        from byterover_integration.bridge import build_brv_memory_content
        result = build_brv_memory_content("add", "user", content="Name: Hieu")
        assert result == "[User profile] Name: Hieu"

    def test_brv_memory_content_add_memory(self):
        from byterover_integration.bridge import build_brv_memory_content
        result = build_brv_memory_content("add", "memory", content="Project uses FastAPI")
        assert result == "[Agent memory] Project uses FastAPI"

    def test_brv_memory_content_replace(self):
        from byterover_integration.bridge import build_brv_memory_content
        result = build_brv_memory_content("replace", "user", content="Name: Bob")
        assert result == "[User profile] Name: Bob"

    def test_brv_memory_content_remove(self):
        from byterover_integration.bridge import build_brv_memory_content
        result = build_brv_memory_content("remove", "memory")
        assert result == " "

    def test_fire_and_forget_calls_curate(self):
        from byterover_integration.bridge import brv_curate_fire_and_forget

        with patch("byterover_integration.bridge.run_brv_curate_sync",
                   return_value={"success": True, "output": "Curated"}) as mock_sync:
            brv_curate_fire_and_forget("[User profile] Name: Hieu")
            assert mock_sync.called
            call_args = mock_sync.call_args[0][0]
            assert call_args[0] == "curate"
            assert "[User profile] Name: Hieu" in call_args

    def test_fire_and_forget_swallows_exceptions(self):
        from byterover_integration.bridge import brv_curate_fire_and_forget
        with patch("byterover_integration.bridge.run_brv_curate_sync",
                   side_effect=RuntimeError("boom")):
            # Should not raise
            brv_curate_fire_and_forget("test content")

    def test_fire_and_forget_logs_error(self):
        from byterover_integration.bridge import brv_curate_fire_and_forget
        with patch("byterover_integration.bridge.run_brv_curate_sync",
                   return_value={"success": False, "error": "Team ID required"}):
            # Should not raise — error is logged, not raised
            brv_curate_fire_and_forget("test content")


# =========================================================================
# run_brv_curate_sync JSON parsing
# =========================================================================

class TestRunBrvCurateSync:
    """Tests for run_brv_curate_sync() JSON event parsing."""

    def test_detects_error_event(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            '{"data":{"event":"thinking"},"success":true}\n'
            '{"data":{"event":"error","message":"Team ID is required"},"success":false}\n'
        )
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run", return_value=mock_result), \
             patch("byterover_integration.client._log_brv_operation"):
            result = run_brv_curate_sync(["curate", "test"])
            assert result["success"] is False
            assert "Team ID" in result["error"]

    def test_detects_completed_event(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            '{"data":{"event":"thinking"},"success":true}\n'
            '{"data":{"event":"completed","operations":[{"type":"create","file":"user-profile.md"}]},"success":true}\n'
        )
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run", return_value=mock_result), \
             patch("byterover_integration.client._log_brv_operation"):
            result = run_brv_curate_sync(["curate", "test"])
            assert result["success"] is True
            assert "create" in result["output"]
            assert "user-profile.md" in result["output"]

    def test_nonzero_exit_code(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "brv crashed"

        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run", return_value=mock_result), \
             patch("byterover_integration.client._log_brv_operation"):
            result = run_brv_curate_sync(["curate", "test"])
            assert result["success"] is False
            assert "crashed" in result["error"]

    def test_injects_format_json_flag(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("byterover_integration.client._log_brv_operation"):
            run_brv_curate_sync(["curate", "test"])
            call_args = mock_run.call_args[0][0]
            assert "--format" in call_args
            assert "json" in call_args

    def test_timeout_handling(self):
        import subprocess as sp
        with patch("shutil.which", return_value="/usr/bin/brv"), \
             patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="brv", timeout=300)), \
             patch("byterover_integration.client._log_brv_operation"):
            result = run_brv_curate_sync(["curate", "test"])
            assert result["success"] is False
            assert "timed out" in result["error"]


# =========================================================================
# Operation logging
# =========================================================================

class TestBrvLogging:
    """Tests for brv operation logging."""

    def test_log_creates_file(self, tmp_path, monkeypatch):
        import byterover_integration.client as br_client
        monkeypatch.setattr(br_client, "get_brv_default_cwd", lambda: tmp_path)
        _reset_brv_file_logger()
        try:
            _log_brv_operation("query test", {"success": True, "output": "ok"}, 1.5)
            log_file = tmp_path / "logs" / "brv.log"
            assert log_file.exists()
            content = log_file.read_text()
            assert "[OK]" in content
            assert "query test" in content
            assert "1.5s" in content
        finally:
            _reset_brv_file_logger()

    def test_log_error_entry(self, tmp_path, monkeypatch):
        import byterover_integration.client as br_client
        monkeypatch.setattr(br_client, "get_brv_default_cwd", lambda: tmp_path)
        _reset_brv_file_logger()
        try:
            _log_brv_operation("curate", {"success": False, "error": "Team ID required"}, 2.0)
            log_file = tmp_path / "logs" / "brv.log"
            content = log_file.read_text()
            assert "[ERROR]" in content
            assert "Team ID required" in content
        finally:
            _reset_brv_file_logger()

