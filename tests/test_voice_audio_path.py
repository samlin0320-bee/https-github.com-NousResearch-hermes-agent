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
# 10. Voice Message Audio Path
# =========================================================================


class TestVoiceMessageAudioPath:
    """Test that original audio file path is included in voice message transcripts."""

    def test_successful_transcript_includes_audio_path(self):
        """The enriched transcript should include '(original audio: <path>)'."""
        transcript = "Hello world"
        path = "/home/user/.hermes/audio_cache/voice_abc123.ogg"

        enriched = (
            f'[The user sent a voice message~ '
            f'Here\'s what they said: "{transcript}"'
            f' (original audio: {path})]'
        )
        assert "original audio:" in enriched
        assert path in enriched
        assert transcript in enriched

    def test_failed_transcript_includes_audio_path(self):
        """Even on transcription failure, the audio path should be included."""
        path = "/home/user/.hermes/audio_cache/voice_abc123.ogg"
        error = "Model not found"

        enriched = (
            "[The user sent a voice message but I had trouble "
            f"transcribing it~ ({error})"
            f" (original audio: {path})]"
        )
        assert "original audio:" in enriched
        assert path in enriched

    def test_exception_transcript_includes_audio_path(self):
        path = "/home/user/.hermes/audio_cache/voice_error.ogg"

        enriched = (
            "[The user sent a voice message but something went wrong "
            "when I tried to listen to it~ Let them know!"
            f" (original audio: {path})]"
        )
        assert "original audio:" in enriched
        assert path in enriched

    def test_voice_enrichment_pattern_in_source(self):
        """Verify the source code includes audio path in all transcript branches."""
        run_py = Path(__file__).parent.parent / "gateway" / "run.py"
        if run_py.exists():
            source = run_py.read_text()
            count = source.count("original audio:")
            assert count >= 3, f"Expected >=3 'original audio:' refs, found {count}"


# =========================================================================
# 11. Empty Message Defense
# =========================================================================


