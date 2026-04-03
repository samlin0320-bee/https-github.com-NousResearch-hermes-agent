"""Method-level tests for CLI /resume history restoration."""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_resume_display import _make_cli


class TestCliResumeCommand:

    def test_handle_resume_command_filters_session_meta(self):
        cli = _make_cli()
        cli.session_id = "current_session"

        mock_db = MagicMock()
        mock_db.get_session.return_value = {"id": "target_session", "title": "Saved Session"}
        mock_db.get_messages_as_conversation.return_value = [
            {"role": "session_meta", "content": None, "tools": []},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        cli._session_db = mock_db

        with patch("hermes_cli.main._resolve_session_by_name_or_id", return_value="target_session"):
            cli._handle_resume_command("/resume Saved Session")

        assert cli.session_id == "target_session"
        assert cli.conversation_history == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        mock_db.reopen_session.assert_called_once_with("target_session")
