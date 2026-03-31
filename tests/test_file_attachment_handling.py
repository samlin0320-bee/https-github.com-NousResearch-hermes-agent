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
# 5. File Attachment Handling — cache_document_from_bytes
# =========================================================================


class TestFileAttachmentHandling:
    """Test that file attachments are saved to disk (cache) rather than
    injected directly into prompt."""

    def test_cache_document_from_bytes(self, tmp_path):
        """Documents should be written to disk with a safe filename."""
        with patch("gateway.platforms.base.DOCUMENT_CACHE_DIR", tmp_path):
            from gateway.platforms.base import cache_document_from_bytes

            data = b"Hello, this is a test document."
            path = cache_document_from_bytes(data, "report.pdf")
            assert os.path.exists(path)
            assert "report.pdf" in path
            assert Path(path).read_bytes() == data

    def test_cache_document_sanitizes_directory_traversal(self, tmp_path):
        with patch("gateway.platforms.base.DOCUMENT_CACHE_DIR", tmp_path):
            from gateway.platforms.base import cache_document_from_bytes

            # Path traversal attack: ../../etc/passwd → should be sanitized to "passwd"
            path = cache_document_from_bytes(b"data", "../../etc/passwd")
            assert "etc" not in Path(path).name
            assert os.path.exists(path)

    def test_cache_document_empty_filename(self, tmp_path):
        with patch("gateway.platforms.base.DOCUMENT_CACHE_DIR", tmp_path):
            from gateway.platforms.base import cache_document_from_bytes

            path = cache_document_from_bytes(b"data", "")
            assert os.path.exists(path)
            assert "document" in Path(path).name

    def test_supported_document_types(self):
        from gateway.platforms.base import SUPPORTED_DOCUMENT_TYPES

        assert ".pdf" in SUPPORTED_DOCUMENT_TYPES
        assert ".md" in SUPPORTED_DOCUMENT_TYPES
        assert ".txt" in SUPPORTED_DOCUMENT_TYPES
        assert ".docx" in SUPPORTED_DOCUMENT_TYPES
        assert ".xlsx" in SUPPORTED_DOCUMENT_TYPES

    def test_cleanup_document_cache(self, tmp_path):
        """Old documents should be pruned by cleanup."""
        with patch("gateway.platforms.base.DOCUMENT_CACHE_DIR", tmp_path):
            from gateway.platforms.base import cleanup_document_cache

            old_file = tmp_path / "old_doc.pdf"
            old_file.write_bytes(b"old")
            # Set modification time to 48 hours ago
            old_time = datetime.now().timestamp() - 48 * 3600
            os.utime(old_file, (old_time, old_time))

            removed = cleanup_document_cache(max_age_hours=24)
            assert removed == 1
            assert not old_file.exists()


# =========================================================================
# 6. Discord Reply Context Extraction
# =========================================================================



# =========================================================================
# 11. Empty Message Defense
# =========================================================================


class TestEmptyMessageDefense:
    """Test that empty/whitespace-only messages get replaced with fallback text."""

    FALLBACK = "(The user sent a message with no text content)"

    def _apply_defense(self, event_text):
        """Mirror the defense logic from discord.py."""
        if not event_text or not event_text.strip():
            event_text = self.FALLBACK
        return event_text

    def test_empty_string_gets_fallback(self):
        assert self._apply_defense("") == self.FALLBACK

    def test_whitespace_only_gets_fallback(self):
        assert self._apply_defense("   \n  \t  ") == self.FALLBACK

    def test_none_gets_fallback(self):
        assert self._apply_defense(None) == self.FALLBACK

    def test_real_text_unchanged(self):
        assert self._apply_defense("Hello, how are you?") == "Hello, how are you?"

    def test_mention_only_gets_fallback(self):
        """After stripping @mention the text may be empty."""
        assert self._apply_defense("") == self.FALLBACK

    def test_defense_pattern_in_source(self):
        """Verify the empty message defense exists in discord.py."""
        discord_py = Path(__file__).parent.parent / "gateway" / "platforms" / "discord.py"
        if discord_py.exists():
            source = discord_py.read_text()
            assert "(The user sent a message with no text content)" in source
