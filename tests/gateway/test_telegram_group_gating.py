import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.config import Platform, PlatformConfig, load_gateway_config


def _make_adapter(require_mention=None, mention_patterns=None):
    from gateway.platforms.telegram import TelegramAdapter

    extra = {}
    if require_mention is not None:
        extra["require_mention"] = require_mention
    if mention_patterns is not None:
        extra["mention_patterns"] = mention_patterns

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="***", extra=extra)
    adapter._bot = SimpleNamespace(id=999, username="example_bot")
    adapter._message_handler = AsyncMock()
    adapter._pending_text_batches = {}
    adapter._pending_text_batch_tasks = {}
    adapter._text_batch_delay_seconds = 0.01
    if mention_patterns is not None:
        os.environ["TELEGRAM_MENTION_PATTERNS"] = "\n".join(mention_patterns)
    else:
        os.environ.pop("TELEGRAM_MENTION_PATTERNS", None)
    if require_mention is not None:
        os.environ["TELEGRAM_REQUIRE_MENTION"] = str(require_mention).lower()
    else:
        os.environ.pop("TELEGRAM_REQUIRE_MENTION", None)
    return adapter


def _group_message(text="hello", *, caption=None, reply_to_bot=False):
    reply_to_message = None
    if reply_to_bot:
        reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=999))
    return SimpleNamespace(
        text=text,
        caption=caption,
        entities=[],
        caption_entities=[],
        chat=SimpleNamespace(id=-100, type="group"),
        reply_to_message=reply_to_message,
    )


def _dm_message(text="hello"):
    return SimpleNamespace(
        text=text,
        caption=None,
        entities=[],
        caption_entities=[],
        chat=SimpleNamespace(id=123, type="private"),
        reply_to_message=None,
    )


def test_group_message_without_mention_is_skipped():
    adapter = _make_adapter(require_mention=True, mention_patterns=[r"^\s*wakeword\b"])

    assert adapter._message_mentions_bot(_group_message("hello everyone")) is False


def test_group_message_with_prefix_is_detected_and_stripped():
    adapter = _make_adapter(require_mention=True, mention_patterns=[r"^\s*wakeword\b"])
    msg = _group_message("wakeword hi there")

    assert adapter._message_mentions_bot(msg) is True
    assert adapter._strip_leading_mention(msg.text) == "hi there"


def test_reply_to_bot_is_detected():
    adapter = _make_adapter(require_mention=True, mention_patterns=[r"^\s*wakeword\b"])

    assert adapter._message_mentions_bot(_group_message("whatever", reply_to_bot=True)) is True


def test_dm_bypasses_group_gating_logic():
    adapter = _make_adapter(require_mention=True, mention_patterns=[r"^\s*wakeword\b"])

    assert adapter._message_mentions_bot(_dm_message("hello")) is False


def test_media_caption_prefix_is_stripped():
    adapter = _make_adapter(require_mention=True, mention_patterns=[r"^\s*wakeword\b"])
    msg = _group_message(text=None, caption="wakeword what is this")

    assert adapter._message_mentions_bot(msg) is True
    assert adapter._strip_leading_mention(msg.caption) == "what is this"


def test_group_media_without_caption_or_reply_is_skipped():
    adapter = _make_adapter(require_mention=True, mention_patterns=[r"^\s*wakeword\b"])
    msg = _group_message(text=None, caption=None)

    assert adapter._message_mentions_bot(msg) is False


def test_config_bridges_telegram_group_settings(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  require_mention: true\n"
        "  mention_patterns:\n"
        "    - \"^\\\\s*wakeword\\\\b\"\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_REQUIRE_MENTION", raising=False)
    monkeypatch.delenv("TELEGRAM_MENTION_PATTERNS", raising=False)

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ["TELEGRAM_REQUIRE_MENTION"] == "true"
    assert __import__("os").environ["TELEGRAM_MENTION_PATTERNS"].splitlines() == [r"^\s*wakeword\b"]
