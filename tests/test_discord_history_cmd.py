"""Tests for custom fork features — bilibili casting, smart home tools,
Discord enhancements, memory limits, file attachments, slash commands, etc.

All tests mock external hardware/APIs so nothing real is contacted.
"""

import json
import os
import socket
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import (
    MagicMock,
    Mock,
    patch,
)

import pytest


# =========================================================================
# 7. /history Slash Command Registration
# =========================================================================


class TestHistoryCommand:
    """Test that the /history command is registered in COMMAND_REGISTRY."""

    def test_history_in_registry(self):
        from hermes_cli.commands import COMMAND_REGISTRY

        names = [cmd.name for cmd in COMMAND_REGISTRY]
        assert "history" in names

    def test_history_command_properties(self):
        from hermes_cli.commands import COMMAND_REGISTRY

        history_cmd = next(c for c in COMMAND_REGISTRY if c.name == "history")
        assert history_cmd.category == "Session"
        assert "history" in history_cmd.description.lower() or "conversation" in history_cmd.description.lower()

    def test_history_is_cli_only(self):
        """History command should be CLI-only (not exposed in gateway by default)."""
        from hermes_cli.commands import COMMAND_REGISTRY

        history_cmd = next(c for c in COMMAND_REGISTRY if c.name == "history")
        assert history_cmd.cli_only is True


# =========================================================================
# 8. Family Chat Filtering
# =========================================================================


