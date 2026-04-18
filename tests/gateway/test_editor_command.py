"""Tests for /editor command in gateway/run.py.

Issue: #7263 - /editor command to compose messages in $EDITOR
"""

import os
import subprocess
import tempfile
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from gateway.platforms.base import MessageEvent, MessageType, MessageSource


class TestEditorCommand:
    """Test cases for _handle_editor_command."""

    @pytest.fixture
    def mock_runner(self):
        """Create a mock GatewayRunner with adapters."""
        from gateway.run import GatewayRunner
        
        # Mock runner with minimal setup
        runner = MagicMock(spec=GatewayRunner)
        runner.adapters = {}
        runner._get_quick_key = lambda source: f"{source.platform.value}:{source.chat_id}"
        
        # Create mock adapter with pending_messages dict
        mock_adapter = MagicMock()
        mock_adapter._pending_messages = {}
        runner.adapters.get = lambda plat: mock_adapter if plat else None
        
        return runner

    @pytest.fixture
    def mock_event(self):
        """Create a mock MessageEvent."""
        source = MessageSource(
            platform=MagicMock(value="telegram"),
            user_id="test_user",
            chat_id="test_chat",
            user_name="Test User",
            chat_type="dm",
        )
        event = MagicMock(spec=MessageEvent)
        event.source = source
        event.message_id = "test_msg_123"
        event.get_command = MagicMock(return_value="editor")
        event.get_command_args = MagicMock(return_value="")
        return event

    @pytest.mark.asyncio
    async def test_editor_command_with_editor_env(self, mock_runner, mock_event):
        """Test /editor command when EDITOR is set."""
        # Set up method reference
        from gateway.run import GatewayRunner
        method = GatewayRunner._handle_editor_command
        
        # Mock editor subprocess that writes content
        def mock_editor_call(cmd):
            # Find the temp file path (last arg)
            tmp_path = cmd[-1]
            with open(tmp_path, "a") as f:
                f.write("This is my test message.\n")
                f.write("Multiple lines work too.\n")
        
        with patch.dict(os.environ, {"EDITOR": "nano"}):
            with patch.object(subprocess, "call", side_effect=mock_editor_call):
                result = await method(mock_runner, mock_event)
        
        assert "chars from editor" in result
        assert "Queued for the next turn" in result

    @pytest.mark.asyncio
    async def test_editor_command_empty_cancels(self, mock_runner, mock_event):
        """Test /editor command when user leaves content empty."""
        from gateway.run import GatewayRunner
        method = GatewayRunner._handle_editor_command
        
        # Mock editor that leaves only comments
        def mock_editor_call(cmd):
            tmp_path = cmd[-1]
            # File already has comment hints, no new content
        
        with patch.dict(os.environ, {"EDITOR": "nano"}):
            with patch.object(subprocess, "call", side_effect=mock_editor_call):
                result = await method(mock_runner, mock_event)
        
        assert "empty content" in result or "cancelled" in result

    @pytest.mark.asyncio
    async def test_editor_command_no_editor_env(self, mock_runner, mock_event):
        """Test /editor command when no EDITOR or VISUAL is set."""
        from gateway.run import GatewayRunner
        method = GatewayRunner._handle_editor_command
        
        # Clear both EDITOR and VISUAL, and mock no nano/vim/vi
        with patch.dict(os.environ, {"EDITOR": "", "VISUAL": ""}, clear=True):
            with patch.object(os.path, "exists", return_value=False):
                result = await method(mock_runner, mock_event)
        
        assert "No editor found" in result

    @pytest.mark.asyncio
    async def test_editor_command_visual_fallback(self, mock_runner, mock_event):
        """Test /editor command uses VISUAL when EDITOR not set."""
        from gateway.run import GatewayRunner
        method = GatewayRunner._handle_editor_command
        
        def mock_editor_call(cmd):
            tmp_path = cmd[-1]
            with open(tmp_path, "a") as f:
                f.write("Content from VISUAL editor.\n")
        
        with patch.dict(os.environ, {"EDITOR": "", "VISUAL": "vim"}):
            with patch.object(subprocess, "call", side_effect=mock_editor_call):
                result = await method(mock_runner, mock_event)
        
        assert "chars from editor" in result

    @pytest.mark.asyncio
    async def test_editor_command_with_args(self, mock_runner, mock_event):
        """Test /editor command handles editor with arguments (e.g. 'code --wait')."""
        from gateway.run import GatewayRunner
        method = GatewayRunner._handle_editor_command
        
        def mock_editor_call(cmd):
            # cmd should be ['code', '--wait', tmp_path]
            assert '--wait' in cmd
            tmp_path = cmd[-1]
            with open(tmp_path, "a") as f:
                f.write("VS Code content.\n")
        
        with patch.dict(os.environ, {"EDITOR": "code --wait"}):
            with patch.object(subprocess, "call", side_effect=mock_editor_call):
                result = await method(mock_runner, mock_event)
        
        assert "chars from editor" in result

    @pytest.mark.asyncio
    async def test_editor_command_strips_comments(self, mock_runner, mock_event):
        """Test /editor command strips comment lines from content."""
        from gateway.run import GatewayRunner
        method = GatewayRunner._handle_editor_command
        
        def mock_editor_call(cmd):
            tmp_path = cmd[-1]
            with open(tmp_path, "a") as f:
                # Mix of comments and real content
                f.write("# This is a comment, should be ignored\n")
                f.write("Real message line 1.\n")
                f.write("# Another comment\n")
                f.write("Real message line 2.\n")
        
        with patch.dict(os.environ, {"EDITOR": "nano"}):
            with patch.object(subprocess, "call", side_effect=mock_editor_call):
                result = await method(mock_runner, mock_event)
        
        # Should have 2 lines, not 4 (comments stripped)
        assert "chars from editor" in result
        # Verify the queued message doesn't contain comments
        adapter = mock_runner.adapters.get(None)
        if adapter and adapter._pending_messages:
            for key, queued_event in adapter._pending_messages.items():
                assert "comment" not in queued_event.text.lower()
                assert "Real message" in queued_event.text


class TestEditorCommandRegistry:
    """Test that /editor is registered in COMMAND_REGISTRY."""

    def test_editor_in_registry(self):
        """editor command should be in COMMAND_REGISTRY."""
        from hermes_cli.commands import COMMAND_REGISTRY, resolve_command
        
        # Find editor command
        editor_cmd = None
        for cmd in COMMAND_REGISTRY:
            if cmd.name == "editor":
                editor_cmd = cmd
                break
        
        assert editor_cmd is not None, "editor command not found in registry"
        assert "EDITOR" in editor_cmd.description or "editor" in editor_cmd.description.lower()
        assert "edit" in editor_cmd.aliases, "edit alias should exist"

    def test_editor_resolve_alias(self):
        """resolve_command should resolve 'edit' alias to 'editor'."""
        from hermes_cli.commands import resolve_command
        
        resolved = resolve_command("edit")
        assert resolved is not None
        assert resolved.name == "editor"

    def test_editor_in_gateway_known_commands(self):
        """editor should be in GATEWAY_KNOWN_COMMANDS."""
        from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS
        
        assert "editor" in GATEWAY_KNOWN_COMMANDS
        assert "edit" in GATEWAY_KNOWN_COMMANDS  # Alias should also be known


if __name__ == "__main__":
    pytest.main([__file__, "-v"])