"""
DingTalk platform adapter using Stream Mode.

Uses dingtalk-stream SDK for real-time message reception without webhooks.
Responses are sent via DingTalk's session webhook (markdown format) or
AI Card streaming for real-time output.

Requires:
    pip install dingtalk-stream httpx
    DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET env vars

Configuration in config.yaml:
    platforms:
      dingtalk:
        enabled: true
        extra:
          client_id: "your-app-key"      # or DINGTALK_CLIENT_ID env var
          client_secret: "your-secret"   # or DINGTALK_CLIENT_SECRET env var
          require_mention: false         # require @mention in groups
          vision_enabled: true           # enable image vision analysis
          ai_card_enabled: true          # enable AI Card streaming
"""

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import dingtalk_stream
    from dingtalk_stream import ChatbotHandler, ChatbotMessage
    DINGTALK_STREAM_AVAILABLE = True
except ImportError:
    DINGTALK_STREAM_AVAILABLE = False
    dingtalk_stream = None  # type: ignore[assignment]

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
    cache_audio_from_bytes,
    cache_document_from_bytes,
    cache_image_from_bytes,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_MESSAGE_LENGTH = 20000
DEDUP_WINDOW_SECONDS = 300
DEDUP_MAX_SIZE = 1000
RECONNECT_BACKOFF_BASE = 5
RECONNECT_BACKOFF_MAX = 60
RECONNECT_MAX_RETRIES = 10
_SESSION_WEBHOOKS_MAX = 500
_WEBHOOK_TTL_SECONDS = 7200  # 2 hours
_DINGTALK_WEBHOOK_RE = re.compile(r'^https://oapi\.dingtalk\.com/')
_DINGTALK_API_HOST = "api.dingtalk.com"

# AI Card template (from OpenClaw reference)
_CARD_TEMPLATE_ID = "51cd8c7e-0e7e-4464-a795-5b81499ada7a.schema"
_CARD_CONTENT_KEY = "content"
_CARD_DEGRADE_DEFAULT_MS = 30 * 60 * 1000  # 30 min
_SENT_CARD_TTL = 7200  # 2 hours to track card outTrackIds for edit_message

# Stop commands
STOP_COMMANDS = {"stop", "/stop", "esc", "quit", "exit", "cancel", "取消", "停止"}

# Emotion reaction payloads for DingTalk emotion API
_EMOTION_THINKING = {
    "emotionName": "thinking",
    "textEmotion": {
        "emotionId": "2659900",
        "emotionName": "thinking",
        "text": "thinking",
        "backgroundId": "im_bg_1",
    },
}
_EMOTION_DONE = {
    "emotionName": "finished",
    "textEmotion": {
        "emotionId": "2659900",
        "emotionName": "finished",
        "text": "finished",
        "backgroundId": "im_bg_1",
    },
}



def check_dingtalk_requirements() -> bool:
    """Check if DingTalk dependencies are available and configured."""
    if not DINGTALK_STREAM_AVAILABLE or not HTTPX_AVAILABLE:
        return False
    if not os.getenv("DINGTALK_CLIENT_ID") or not os.getenv("DINGTALK_CLIENT_SECRET"):
        return False
    return True


class DingTalkAdapter(BasePlatformAdapter):
    """DingTalk chatbot adapter using Stream Mode.

    Features:
    - Real-time message reception via WebSocket (Stream Mode)
    - Text, image, file, voice message handling
    - Image download + vision analysis
    - @mention detection for group chats
    - Session webhook markdown replies
    - AI Card streaming for real-time output
    - Interactive ActionCard for exec approval / model picker
    - Access token management for DingTalk API calls
    """

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.DINGTALK)

        extra = config.extra or {}
        self._client_id: str = extra.get("client_id") or os.getenv("DINGTALK_CLIENT_ID", "")
        self._client_secret: str = extra.get("client_secret") or os.getenv("DINGTALK_CLIENT_SECRET", "")

        # Access token (v1.0 API)
        self._access_token: Optional[str] = None
        self._token_expire_time: float = 0

        self._stream_client: Any = None
        self._stream_task: Optional[asyncio.Task] = None
        self._http_client: Optional["httpx.AsyncClient"] = None

        # Message deduplication
        self._seen_messages: Dict[str, float] = {}
        self._last_cleanup: float = 0

        # msg_id → conversation_id mapping for emotion API
        self._msg_conversations: Dict[str, str] = {}
        # chat_id → sender_id for proactive DM sends
        self._chat_senders: Dict[str, str] = {}
        # chat_id → chat_type ("group" or "dm") for proactive API routing
        self._chat_types: Dict[str, str] = {}

        # Text message batching (merge rapid-fire messages)
        self._pending_text_batches: Dict[str, MessageEvent] = {}
        self._pending_text_batch_tasks: Dict[str, asyncio.Task] = {}
        self._text_batch_delay_seconds: float = float(os.getenv("DINGTALK_TEXT_BATCH_DELAY", "0.6"))

        # Photo batching (merge rapid-fire image sends)
        self._pending_photo_batches: Dict[str, MessageEvent] = {}
        self._pending_photo_batch_tasks: Dict[str, asyncio.Task] = {}
        self._photo_batch_delay_seconds: float = float(os.getenv("DINGTALK_PHOTO_BATCH_DELAY", "0.8"))

        # Session webhooks: chat_id -> (webhook_url, expiry_time)
        self._session_webhooks: Dict[str, str] = {}
        self._webhook_expiry: Dict[str, float] = {}

        # Config flags
        self._require_mention: bool = extra.get("require_mention", False)
        self._allowed_senders: Optional[List[str]] = extra.get("allowed_senders")
        self._vision_enabled: bool = extra.get("vision_enabled", True)

        # AI Card config
        self._ai_card_enabled: bool = extra.get("ai_card_enabled", False)
        self._ai_card_degrade_ms: int = extra.get("ai_card_degrade_ms", _CARD_DEGRADE_DEFAULT_MS)
        self._ai_card_degrade_until: float = 0  # timestamp to retry AI Card after degrade
        self._ai_card_instances: Dict[str, Dict[str, Any]] = {}  # chat_id -> card state
        # Track sent card outTrackIds for edit_message lookup (outTrackId -> (chat_id, expire))
        self._sent_card_tracks: Dict[str, Tuple[str, float]] = {}

        # @mention patterns
        self._mention_pattern = re.compile(
            r'@\s*(?:hermes|助手|机器人|bot|ai|claude|gpt|' +
            re.escape(self._client_id[:8] if self._client_id else '') +
            r')',
            re.IGNORECASE,
        )

    # ================================================================
    # Connection lifecycle
    # ================================================================

    async def connect(self) -> bool:
        """Connect to DingTalk via Stream Mode."""
        if not DINGTALK_STREAM_AVAILABLE:
            logger.warning("[%s] dingtalk-stream not installed. Run: pip install dingtalk-stream", self.name)
            return False
        if not HTTPX_AVAILABLE:
            logger.warning("[%s] httpx not installed. Run: pip install httpx", self.name)
            return False
        if not self._client_id or not self._client_secret:
            logger.warning("[%s] DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET required", self.name)
            return False

        try:
            self._http_client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )

            # Pre-fetch access token
            await self._refresh_access_token()

            credential = dingtalk_stream.Credential(self._client_id, self._client_secret)
            self._stream_client = dingtalk_stream.DingTalkStreamClient(credential)

            handler = _IncomingHandler(self)
            self._stream_client.register_callback_handler(
                dingtalk_stream.ChatbotMessage.TOPIC, handler,
            )

            self._running = True
            self._stream_task = asyncio.create_task(self._run_stream())
            self._mark_connected()
            logger.info("[%s] Connected via Stream Mode", self.name)
            return True
        except Exception as e:
            logger.error("[%s] Failed to connect: %s", self.name, e)
            return False

    async def _run_stream(self) -> None:
        """Run the stream client with exponential backoff reconnection."""
        retries = 0
        while self._running:
            try:
                connect_time = time.time()
                logger.debug("[%s] Starting stream client...", self.name)
                await self._stream_client.start()
                # Reset retries if connection was stable for > 2 minutes
                if time.time() - connect_time > 120:
                    retries = 0
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._running:
                    return
                logger.warning("[%s] Stream client error: %s", self.name, e)

            if not self._running:
                return

            retries += 1
            if retries > RECONNECT_MAX_RETRIES:
                msg = (
                    f"DingTalk stream could not reconnect after {RECONNECT_MAX_RETRIES} "
                    f"retries. Restarting gateway."
                )
                logger.error("[%s] %s", self.name, msg)
                self._set_fatal_error("dingtalk_network_error", msg, retryable=True)
                return

            delay = min(RECONNECT_BACKOFF_BASE * (2 ** (retries - 1)), RECONNECT_BACKOFF_MAX)
            logger.info("[%s] Reconnecting in %ds (attempt %d/%d)...", self.name, delay, retries, RECONNECT_MAX_RETRIES)
            await asyncio.sleep(delay)

    async def disconnect(self) -> None:
        """Disconnect from DingTalk."""
        self._running = False
        self._mark_disconnected()

        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        self._msg_conversations.clear()
        self._chat_senders.clear()
        self._chat_types.clear()
        for task in self._pending_text_batch_tasks.values():
            task.cancel()
        self._pending_text_batches.clear()
        self._pending_text_batch_tasks.clear()
        for task in self._pending_photo_batch_tasks.values():
            task.cancel()
        self._pending_photo_batches.clear()
        self._pending_photo_batch_tasks.clear()

        self._stream_client = None
        self._session_webhooks.clear()
        self._webhook_expiry.clear()
        self._seen_messages.clear()
        self._ai_card_instances.clear()
        self._sent_card_tracks.clear()
        logger.info("[%s] Disconnected", self.name)

    # ================================================================
    # Access token management (v1.0 API)
    # ================================================================

    async def _refresh_access_token(self) -> bool:
        """Refresh DingTalk access token via v1.0 API."""
        if time.time() < self._token_expire_time - 60:
            return True

        if not self._http_client:
            return False

        try:
            resp = await self._http_client.post(
                f"https://{_DINGTALK_API_HOST}/v1.0/oauth2/accessToken",
                json={"appKey": self._client_id, "appSecret": self._client_secret},
            )
            data = resp.json()
            if "accessToken" in data:
                self._access_token = data["accessToken"]
                self._token_expire_time = time.time() + data.get("expireIn", 7200)
                logger.debug("[%s] Access token refreshed", self.name)
                return True
            else:
                # Fallback to legacy /gettoken endpoint
                logger.warning("[%s] v1.0 token API failed, trying legacy: %s", self.name, data)
                return await self._refresh_access_token_legacy()
        except Exception as e:
            logger.warning("[%s] Token refresh error, trying legacy: %s", self.name, e)
            return await self._refresh_access_token_legacy()

    async def _refresh_access_token_legacy(self) -> bool:
        """Fallback: refresh token via legacy /gettoken endpoint."""
        if not self._http_client:
            return False
        try:
            resp = await self._http_client.get(
                f"https://oapi.dingtalk.com/gettoken",
                params={"appkey": self._client_id, "appsecret": self._client_secret},
            )
            data = resp.json()
            if data.get("errcode") == 0:
                self._access_token = data["access_token"]
                self._token_expire_time = time.time() + data.get("expires_in", 7200)
                logger.debug("[%s] Access token refreshed (legacy)", self.name)
                return True
            logger.error("[%s] Token refresh failed: %s", self.name, data)
            return False
        except Exception as e:
            logger.error("[%s] Legacy token refresh error: %s", self.name, e)
            return False

    # ================================================================
    # Deduplication
    # ================================================================

    def _is_duplicate(self, msg_id: str) -> bool:
        """Check and record a message ID. Returns True if already seen."""
        now = time.time()
        if now - self._last_cleanup > 60:
            self._cleanup_seen_messages(now)
            self._last_cleanup = now

        if msg_id in self._seen_messages:
            return True

        self._seen_messages[msg_id] = now
        if len(self._seen_messages) > DEDUP_MAX_SIZE:
            self._cleanup_seen_messages(now)
        return False

    def _cleanup_seen_messages(self, now: float) -> None:
        """Remove expired message IDs from dedup cache."""
        cutoff = now - DEDUP_WINDOW_SECONDS
        expired = [k for k, v in self._seen_messages.items() if v < cutoff]
        for k in expired:
            del self._seen_messages[k]
            self._msg_conversations.pop(k, None)

    # ================================================================
    # Webhook management
    # ================================================================

    def _save_webhook(self, chat_id: str, webhook: str) -> None:
        """Save session webhook with expiry tracking."""
        if len(self._session_webhooks) >= _SESSION_WEBHOOKS_MAX:
            oldest_key = min(self._webhook_expiry, key=self._webhook_expiry.get, default=None)
            if oldest_key:
                self._session_webhooks.pop(oldest_key, None)
                self._webhook_expiry.pop(oldest_key, None)

        self._session_webhooks[chat_id] = webhook
        self._webhook_expiry[chat_id] = time.time() + _WEBHOOK_TTL_SECONDS

    def _get_webhook(self, chat_id: str) -> Optional[str]:
        """Get valid session webhook for chat."""
        webhook = self._session_webhooks.get(chat_id)
        if not webhook:
            return None
        expiry = self._webhook_expiry.get(chat_id, 0)
        if time.time() > expiry:
            self._session_webhooks.pop(chat_id, None)
            self._webhook_expiry.pop(chat_id, None)
            return None
        return webhook

    # ================================================================
    # Inbound message processing
    # ================================================================

    async def _on_message(self, message: "ChatbotMessage") -> None:
        """Process an incoming DingTalk chatbot message."""
        msg_id = getattr(message, "message_id", None) or uuid.uuid4().hex
        if self._is_duplicate(msg_id):
            logger.debug("[%s] Duplicate message %s, skipping", self.name, msg_id)
            return

        # Extract content based on type
        content_type, content_data = self._extract_content(message)
        if not content_data and content_type == "text":
            logger.debug("[%s] Empty message, skipping", self.name)
            return

        # Chat context
        conversation_id = getattr(message, "conversation_id", "") or ""
        conversation_type = getattr(message, "conversation_type", "1")
        is_group = str(conversation_type) == "2"
        sender_id = getattr(message, "sender_id", "") or ""
        sender_nick = getattr(message, "sender_nick", "") or sender_id
        sender_staff_id = getattr(message, "sender_staff_id", "") or ""

        chat_id = conversation_id or sender_id
        chat_type = "group" if is_group else "dm"

        # Store conversation_id for emotion API (needs original openConversationId)
        if msg_id and conversation_id:
            self._msg_conversations[msg_id] = conversation_id
        # Store sender_staff_id for proactive DM sends (staff_id format works as userIds)
        if chat_id and sender_staff_id:
            self._chat_senders[chat_id] = sender_staff_id
        # Store chat type (group/dm) for proactive API routing
        if chat_id:
            self._chat_types[chat_id] = chat_type

        # Check allowed senders
        if self._allowed_senders and sender_staff_id not in self._allowed_senders:
            logger.warning("[%s] Unauthorized sender: %s", self.name, sender_staff_id)
            return

        # Store session webhook
        session_webhook = getattr(message, "session_webhook", None) or ""
        if session_webhook and chat_id and _DINGTALK_WEBHOOK_RE.match(session_webhook):
            self._save_webhook(chat_id, session_webhook)

        # Dispatch by content type
        if content_type == "image":
            await self._handle_image_message(
                message, chat_id, chat_type, sender_id, sender_nick,
                sender_staff_id, content_data, session_webhook, msg_id,
            )
        elif content_type == "file":
            await self._handle_file_message(
                message, chat_id, chat_type, sender_id, sender_nick,
                sender_staff_id, content_data, session_webhook, msg_id,
            )
        elif content_type == "voice":
            await self._handle_voice_message(
                message, chat_id, chat_type, sender_id, sender_nick,
                sender_staff_id, content_data, session_webhook, msg_id,
            )
        else:
            await self._handle_text_message(
                message, chat_id, chat_type, sender_id, sender_nick,
                sender_staff_id, content_data, session_webhook, msg_id,
            )

    def _extract_content(self, message: "ChatbotMessage") -> Tuple[str, Any]:
        """Extract content from message, return (content_type, content_data)."""
        # Debug: log all attributes for non-text messages
        msgtype = getattr(message, "msgtype", None) or getattr(message, "msg_type", None)
        if msgtype and msgtype != "text":
            logger.debug(
                "[%s] Non-text msgtype=%s attrs=%s",
                self.name, msgtype,
                {k: repr(v)[:100] for k, v in vars(message).items() if not k.startswith('_')}
            )

        # Try text field first
        text = getattr(message, "text", None)
        if text is not None:
            if hasattr(text, "content"):
                content = str(text.content or "").strip()
            elif isinstance(text, dict):
                content = text.get("content", "").strip()
            else:
                content = str(text).strip()
            if content:
                return "text", content

        # Try content field (image, file, voice)
        raw_content = getattr(message, "content", None)
        if raw_content and isinstance(raw_content, dict):
            if "pictureDownloadCode" in raw_content:
                return "image", raw_content
            if "fileDownloadCode" in raw_content or "downloadCode" in raw_content:
                if raw_content.get("fileName", "").lower().endswith(
                    (".amr", ".mp3", ".wav", ".ogg", ".m4a", ".aac")
                ):
                    return "voice", raw_content
                return "file", raw_content
            # Voice messages: have downloadCode and recognition
            if "recognition" in raw_content:
                return "voice", raw_content
            if "content" in raw_content:
                return "text", raw_content.get("content", "").strip()

        # Fallback: rich_text_content
        rich_text = getattr(message, "rich_text_content", None)
        if rich_text and isinstance(rich_text, list):
            parts = []
            for item in rich_text:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "text")
                if item_type == "text":
                    parts.append(item.get("text", item.get("content", "")))
                elif item_type == "at":
                    parts.append(f"@{item.get('atName', '某人')}")
                elif item_type == "picture":
                    parts.append("[图片]")
            content = " ".join(p for p in parts if p).strip()
            if content:
                return "text", content

        return "text", ""

    async def _handle_text_message(
        self, message, chat_id, chat_type, sender_id, sender_nick,
        sender_staff_id, text, session_webhook, msg_id,
    ) -> None:
        """Handle text message, including stop commands and @mentions."""
        # Check stop command
        if isinstance(text, str) and text.strip().lower() in STOP_COMMANDS:
            await self._handle_stop_command(chat_id, sender_nick)
            return

        # Group @mention check
        is_group = chat_type == "group"
        if is_group and self._require_mention:
            if not self._mention_pattern.search(text):
                logger.debug("[%s] No @mention in group message, skipping", self.name)
                return

        # Clean @mentions from text
        clean_text = self._clean_bot_trigger_text(text)

        # Approval keyword mapping (Chinese → English command for gateway routing)
        _clean_lower = clean_text.strip().lower()
        if _clean_lower in ("批准", "/批准"):
            clean_text = "/approve"
        elif _clean_lower in ("拒绝", "/拒绝"):
            clean_text = "/deny"

        # Extract quoted reply context
        reply_to_text = self._extract_quoted_text(message)

        source = self.build_source(
            chat_id=chat_id,
            chat_name=getattr(message, "conversation_title", None),
            chat_type=chat_type,
            user_id=sender_id,
            user_name=sender_nick,
            user_id_alt=sender_staff_id if sender_staff_id else None,
        )

        timestamp = self._parse_timestamp(getattr(message, "create_at", None))

        event = MessageEvent(
            text=clean_text,
            message_type=MessageType.TEXT,
            source=source,
            message_id=msg_id,
            raw_message=message,
            timestamp=timestamp,
            reply_to_text=reply_to_text,
        )

        logger.debug(
            "[%s] Message from %s in %s: %s",
            self.name, sender_nick, chat_id[:20] if chat_id else "?", clean_text[:50],
        )
        self._enqueue_text_event(event)

    # ------------------------------------------------------------------
    # Text message batching (merge rapid-fire messages)
    # ------------------------------------------------------------------

    def _text_batch_key(self, event: MessageEvent) -> str:
        """Batch key = chat_id (DM: per user, Group: per group)."""
        return event.source.chat_id or "dm"

    def _enqueue_text_event(self, event: MessageEvent) -> None:
        """Buffer a text event and reset the flush timer."""
        key = self._text_batch_key(event)
        existing = self._pending_text_batches.get(key)
        if existing is None:
            self._pending_text_batches[key] = event
        elif event.text:
            existing.text = f"{existing.text}\n{event.text}" if existing.text else event.text

        prior_task = self._pending_text_batch_tasks.get(key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        self._pending_text_batch_tasks[key] = asyncio.create_task(
            self._flush_text_batch(key)
        )

    async def _flush_text_batch(self, key: str) -> None:
        """Wait for quiet period then dispatch the aggregated text."""
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._text_batch_delay_seconds)
            event = self._pending_text_batches.pop(key, None)
            if not event:
                return
            logger.info(
                "[%s] Flushing text batch %s (%d chars)",
                self.name, key, len(event.text or ""),
            )
            await self.handle_message(event)
        finally:
            if self._pending_text_batch_tasks.get(key) is current_task:
                self._pending_text_batch_tasks.pop(key, None)

    # ------------------------------------------------------------------
    # Photo batching (merge rapid-fire image sends)
    # ------------------------------------------------------------------

    def _enqueue_photo_event(self, batch_key: str, event: MessageEvent) -> None:
        """Merge photo events into a pending batch and schedule flush."""
        existing = self._pending_photo_batches.get(batch_key)
        if existing is None:
            self._pending_photo_batches[batch_key] = event
        else:
            existing.media_urls.extend(event.media_urls)
            existing.media_types.extend(event.media_types)
            if event.text:
                existing.text = f"{existing.text}\n{event.text}" if existing.text else event.text

        prior_task = self._pending_photo_batch_tasks.get(batch_key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        self._pending_photo_batch_tasks[batch_key] = asyncio.create_task(
            self._flush_photo_batch(batch_key)
        )

    async def _flush_photo_batch(self, batch_key: str) -> None:
        """Wait for quiet period then dispatch the aggregated photos."""
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._photo_batch_delay_seconds)
            event = self._pending_photo_batches.pop(batch_key, None)
            if not event:
                return
            logger.info(
                "[%s] Flushing photo batch %s with %d image(s)",
                self.name, batch_key, len(event.media_urls),
            )
            await self.handle_message(event)
        finally:
            if self._pending_photo_batch_tasks.get(batch_key) is current_task:
                self._pending_photo_batch_tasks.pop(batch_key, None)

    async def _handle_image_message(
        self, message, chat_id, chat_type, sender_id, sender_nick,
        sender_staff_id, content_data, session_webhook, msg_id,
    ) -> None:
        """Handle image message: download, cache, analyze with vision."""
        download_code = content_data.get("downloadCode") or content_data.get("pictureDownloadCode")
        picture_code = content_data.get("pictureDownloadCode")  # legacy API only

        logger.info(
            "[%s] Image message: download_code=%s picture_code=%s content_keys=%s",
            self.name,
            download_code[:20] if download_code else None,
            picture_code[:20] if picture_code else None,
            list(content_data.keys()),
        )

        if not download_code or not self._vision_enabled:
            # No download or vision disabled — forward as placeholder
            caption = content_data.get("caption", "")
            text = f"[图片] {caption}".strip() if caption else "[图片]"
            await self._handle_text_message(
                message, chat_id, chat_type, sender_id, sender_nick,
                sender_staff_id, text, session_webhook, msg_id,
            )
            return

        # Download and analyze image
        image_path = await self._download_media(download_code, "image", legacy_code=picture_code)
        if not image_path:
            logger.warning("[%s] Failed to download image", self.name)
            await self._handle_text_message(
                message, chat_id, chat_type, sender_id, sender_nick,
                sender_staff_id, "[图片下载失败]", session_webhook, msg_id,
            )
            return

        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            ext = Path(image_path).suffix or ".jpg"
            cached_path = cache_image_from_bytes(image_bytes, ext=ext)

            source = self.build_source(
                chat_id=chat_id,
                chat_name=getattr(message, "conversation_title", None),
                chat_type=chat_type,
                user_id=sender_id,
                user_name=sender_nick,
                user_id_alt=sender_staff_id if sender_staff_id else None,
            )

            timestamp = self._parse_timestamp(getattr(message, "create_at", None))

            caption = content_data.get("caption", "")
            text = caption if caption else ""

            event = MessageEvent(
                text=text,
                message_type=MessageType.PHOTO,
                source=source,
                message_id=msg_id,
                raw_message=message,
                timestamp=timestamp,
                media_urls=[cached_path],
                media_types=["image"],
            )

            logger.info("[%s] Cached image from %s at %s", self.name, sender_nick, cached_path)
            self._enqueue_photo_event(chat_id, event)
        except Exception as e:
            logger.error("[%s] Image processing error: %s", self.name, e)
        finally:
            # Clean up temp download file
            try:
                os.unlink(image_path)
            except OSError:
                pass

    async def _handle_file_message(
        self, message, chat_id, chat_type, sender_id, sender_nick,
        sender_staff_id, content_data, session_webhook, msg_id,
    ) -> None:
        """Handle file message: download and cache."""
        download_code = content_data.get("fileDownloadCode") or content_data.get("downloadCode")
        file_name = content_data.get("fileName", "unknown")

        if not download_code:
            text = f"[文件: {file_name}]"
            await self._handle_text_message(
                message, chat_id, chat_type, sender_id, sender_nick,
                sender_staff_id, text, session_webhook, msg_id,
            )
            return

        file_path = await self._download_media(download_code, "file")
        if not file_path:
            logger.warning("[%s] Failed to download file: %s", self.name, file_name)
            await self._handle_text_message(
                message, chat_id, chat_type, sender_id, sender_nick,
                sender_staff_id, f"[文件下载失败: {file_name}]", session_webhook, msg_id,
            )
            return

        try:
            # Cache to document cache directory so agent can access it
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            cached_path = cache_document_from_bytes(file_bytes, file_name)
            logger.info("[%s] File cached to: %s (%d bytes)", self.name, cached_path, len(file_bytes))

            source = self.build_source(
                chat_id=chat_id,
                chat_name=getattr(message, "conversation_title", None),
                chat_type=chat_type,
                user_id=sender_id,
                user_name=sender_nick,
                user_id_alt=sender_staff_id if sender_staff_id else None,
            )

            timestamp = self._parse_timestamp(getattr(message, "create_at", None))

            event = MessageEvent(
                text=f"[文件: {file_name}] (path: {cached_path})",
                message_type=MessageType.DOCUMENT,
                source=source,
                message_id=msg_id,
                raw_message=message,
                timestamp=timestamp,
                media_urls=[cached_path],
                media_types=["document"],
            )

            logger.info("[%s] Cached file from %s: %s", self.name, sender_nick, file_name)
            await self.handle_message(event)
        except Exception as e:
            logger.error("[%s] File processing error: %s", self.name, e)
        finally:
            try:
                os.unlink(file_path)
            except OSError:
                pass

    async def _handle_voice_message(
        self, message, chat_id, chat_type, sender_id, sender_nick,
        sender_staff_id, content_data, session_webhook, msg_id,
    ) -> None:
        """Handle voice message: use recognition text + download audio."""
        recognition = content_data.get("recognition", "").strip()
        download_code = content_data.get("downloadCode", "")

        # Build text from recognition
        voice_text = recognition if recognition else ""

        # Download audio file if available
        audio_path = None
        cached_audio = None
        media_urls = []
        media_types = []
        if download_code:
            audio_path = await self._download_media(download_code, "voice")
            if audio_path:
                try:
                    with open(audio_path, "rb") as f:
                        audio_bytes = f.read()
                    ext = os.path.splitext(audio_path)[1] or ".amr"
                    cached_audio = cache_audio_from_bytes(audio_bytes, ext=ext)
                    media_urls = [cached_audio]
                    media_types = ["audio"]
                except Exception as e:
                    logger.warning("[%s] Audio cache error: %s", self.name, e)

        try:
            source = self.build_source(
                chat_id=chat_id,
                chat_name=getattr(message, "conversation_title", None),
                chat_type=chat_type,
                user_id=sender_id,
                user_name=sender_nick,
                user_id_alt=sender_staff_id if sender_staff_id else None,
            )

            timestamp = self._parse_timestamp(getattr(message, "create_at", None))

            event = MessageEvent(
                text=voice_text,
                message_type=MessageType.VOICE,
                source=source,
                message_id=msg_id,
                raw_message=message,
                timestamp=timestamp,
                media_urls=media_urls,
                media_types=media_types,
            )

            logger.info("[%s] Voice from %s: %s", self.name, sender_nick, voice_text[:50] if voice_text else "(no recognition)")
            await self.handle_message(event)
        except Exception as e:
            logger.error("[%s] Voice processing error: %s", self.name, e)
        finally:
            if audio_path:
                try:
                    os.unlink(audio_path)
                except OSError:
                    pass

    # ================================================================
    # Media download
    # ================================================================

    _EXT_MAP = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
        "image/gif": ".gif", "image/webp": ".webp",
        "audio/amr": ".amr", "audio/mp3": ".mp3", "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg", "audio/wav": ".wav",
    }

    async def _download_media(self, download_code: str, media_type: str, legacy_code: str = "") -> Optional[str]:
        """Download media from DingTalk using download code. Returns local file path.

        Args:
            download_code: Code for new API (/v1.0/robot/messageFiles/download)
            media_type: "image", "file", "voice", etc.
            legacy_code: Code for legacy API (/media/download), defaults to download_code
        """
        if not await self._refresh_access_token():
            logger.error("[%s] Cannot download media: no valid token", self.name)
            return None
        if not self._http_client:
            return None

        try:
            # Try new API first (works for all media types)
            url = f"https://{_DINGTALK_API_HOST}/v1.0/robot/messageFiles/download"
            logger.info("[%s] Trying new download API: code=%s type=%s", self.name, download_code[:20], media_type)
            resp = await self._http_client.post(
                url,
                headers={"x-acs-dingtalk-access-token": self._access_token or ""},
                json={"downloadCode": download_code, "robotCode": self._client_id},
            )
            if resp.status_code == 200:
                payload = resp.json()
                download_url = payload.get("downloadUrl") or (payload.get("body") or {}).get("downloadUrl")
                safe_payload = {k: (v[:50] if isinstance(v, str) and len(v) > 50 else v) for k, v in payload.items()}
                logger.info("[%s] New API response: %s", self.name, safe_payload)
                if download_url:
                    from urllib.parse import urlparse
                    parsed = urlparse(download_url)
                    if parsed.hostname and not any(
                        parsed.hostname.endswith(d) for d in ("dingtalk.com", "alicdn.com", "aliyuncs.com")
                    ):
                        logger.warning("[%s] Blocked download from untrusted host: %s", self.name, parsed.hostname)
                        return None
                    file_resp = await self._http_client.get(download_url, timeout=30.0)
                    if file_resp.status_code == 200:
                        ct = file_resp.headers.get("content-type", "").lower().split(";")[0].strip()
                        ext = self._EXT_MAP.get(ct, ".dat")
                        return self._save_temp_file(file_resp.content, ext)
            logger.warning("[%s] New download API failed (HTTP %d): %s, trying legacy", self.name, resp.status_code, resp.text[:200])

            # Legacy media download fallback
            url = "https://oapi.dingtalk.com/media/download"
            legacy_dl_code = legacy_code or download_code
            logger.info("[%s] Trying legacy download API: code=%s", self.name, legacy_dl_code[:20])
            params = {"access_token": self._access_token, "downloadCode": legacy_dl_code}
            resp = await self._http_client.get(url, params=params, timeout=30.0)

            if resp.status_code != 200:
                logger.warning("[%s] Media download failed: HTTP %d", self.name, resp.status_code)
                return None

            # DingTalk may return HTTP 200 with JSON error body instead of binary
            content_type = resp.headers.get("content-type", "").lower()
            raw = resp.content
            if content_type.startswith("application/json") or (raw[:1] == b"{"):
                try:
                    err = resp.json()
                    logger.warning("[%s] Media download API error: %s", self.name, err)
                except Exception:
                    logger.warning("[%s] Media download returned non-binary data", self.name)
                return None

            ext = self._EXT_MAP.get(content_type, ".dat")
            return self._save_temp_file(resp.content, ext)

        except Exception as e:
            logger.error("[%s] Media download error: %s", self.name, e)
            return None

    # ================================================================
    # Outbound messaging
    # ================================================================

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a markdown reply via DingTalk session webhook."""
        metadata = metadata or {}

        # If an active AI Card exists for this chat, stream to it and finalize
        if self._ai_card_enabled and chat_id in self._ai_card_instances:
            ok = await self.ai_card_stream_update(chat_id, content, is_final=True)
            if ok:
                return SendResult(success=True, message_id="")

        # Check if AI Card should be used (explicit opt-in)
        if self._ai_card_enabled and metadata.get("use_ai_card", False):
            if time.time() > self._ai_card_degrade_until:
                result = await self._send_via_ai_card(chat_id, content, metadata)
                if result.success:
                    return result
                # Degrade to markdown
                self._ai_card_degrade_until = time.time() + self._ai_card_degrade_ms / 1000
                logger.warning("[%s] AI Card failed, degrading to markdown", self.name)

        session_webhook = metadata.get("session_webhook") or self._get_webhook(chat_id)
        if not session_webhook:
            return SendResult(
                success=False,
                error="No session_webhook available. Reply must follow an incoming message.",
            )

        if not self._http_client:
            return SendResult(success=False, error="HTTP client not initialized")

        content = self.format_message(content)

        # Check for ActionCard
        if metadata.get("action_card"):
            payload = self._build_action_card(metadata["action_card"], content)
        else:
            payload = {
                "msgtype": "markdown",
                "markdown": {"title": "Hermes", "text": content[:self.MAX_MESSAGE_LENGTH]},
            }

        try:
            resp = await self._http_client.post(session_webhook, json=payload, timeout=15.0)
            if resp.status_code < 300:
                result_data = resp.json() if resp.text else {}
                if result_data.get("errcode", 0) == 0:
                    return SendResult(success=True, message_id=result_data.get("messageId", uuid.uuid4().hex[:12]))
                return SendResult(success=False, error=f"DingTalk error: {result_data}")
            body = resp.text
            logger.warning("[%s] Send failed HTTP %d: %s", self.name, resp.status_code, body[:200])
            return SendResult(success=False, error=f"HTTP {resp.status_code}: {body[:200]}")
        except httpx.TimeoutException:
            return SendResult(success=False, error="Timeout sending message to DingTalk", retryable=True)
        except Exception as e:
            logger.error("[%s] Send error: %s", self.name, e)
            return SendResult(success=False, error=str(e), retryable=True)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send image via markdown embed (DingTalk webhook supports markdown images)."""
        metadata = metadata or {}
        content = f"![image]({image_url})"
        if caption:
            content = f"{caption}\n\n{content}"
        return await self.send(chat_id, content, reply_to=reply_to, metadata=metadata)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Upload local image to DingTalk media server and send."""
        metadata = metadata or {}
        media_id = await self._upload_media(image_path, "image")
        if media_id:
            content = f"![image]({media_id})"
            if caption:
                content = f"{caption}\n\n{content}"
            return await self.send(chat_id, content, reply_to=reply_to, metadata=metadata)
        # Fallback: mention the path
        text = f"🖼️ Image: {os.path.basename(image_path)}"
        if caption:
            text = f"{caption}\n{text}"
        return await self.send(chat_id, text, reply_to=reply_to, metadata=metadata)

    async def send_animation(
        self,
        chat_id: str,
        animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send animated GIF via proactive API (sampleImageMsg) for inline playback."""
        import tempfile
        temp_path = None
        try:
            # Download GIF if it's a URL
            local_path = animation_url
            if animation_url.startswith(("http://", "https://")):
                if not self._http_client:
                    return await self.send_image(chat_id, animation_url, caption, reply_to, metadata)
                resp = await self._http_client.get(animation_url, timeout=30.0)
                resp.raise_for_status()
                suffix = ".gif" if ".gif" in animation_url.lower() else ""
                fd, temp_path = tempfile.mkstemp(suffix=suffix)
                with os.fdopen(fd, "wb") as f:
                    f.write(resp.content)
                local_path = temp_path

            if not os.path.exists(local_path):
                return await self.send_image(chat_id, animation_url, caption, reply_to, metadata)

            media_id = await self._upload_media(local_path, "image")
            if not media_id:
                return await self.send_image(chat_id, animation_url, caption, reply_to, metadata)

            result = await self._send_proactive_media(
                chat_id, media_id, "sampleImageMsg",
                extra={"photoURL": media_id},
            )
            if result.success and caption:
                await self.send(chat_id, caption, reply_to=reply_to, metadata=metadata)
            return result
        except Exception as e:
            logger.warning("[%s] send_animation failed, falling back to send_image: %s", self.name, e)
            return await self.send_image(chat_id, animation_url, caption, reply_to, metadata)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send audio as a native DingTalk voice message via proactive API."""
        if not os.path.exists(audio_path):
            return SendResult(success=False, error=f"Audio file not found: {audio_path}")

        media_id = await self._upload_media(audio_path, "voice")
        if not media_id:
            return await super().send_voice(chat_id, audio_path, caption, reply_to)

        duration_ms = await self._get_audio_duration_ms(audio_path)
        return await self._send_proactive_media(
            chat_id, media_id, "sampleAudio",
            extra={"mediaId": media_id, "duration": str(duration_ms)},
        )

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a video natively via DingTalk proactive API."""
        if not os.path.exists(video_path):
            return SendResult(success=False, error=f"Video file not found: {video_path}")

        media_id = await self._upload_media(video_path, "video")
        if not media_id:
            return await super().send_video(chat_id, video_path, caption, reply_to)

        filename = os.path.basename(video_path)
        ext = os.path.splitext(video_path)[1].lstrip(".") or "mp4"
        return await self._send_proactive_media(
            chat_id, media_id, "sampleFile",
            extra={"mediaId": media_id, "fileName": filename, "fileType": ext},
        )

    async def _send_proactive_media(
        self,
        chat_id: str,
        media_id: str,
        msg_key: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send media via DingTalk proactive message API (groupMessages/send or oToMessages/batchSend)."""
        if not await self._refresh_access_token():
            return SendResult(success=False, error="No access token")
        if not self._http_client:
            return SendResult(success=False, error="HTTP client not initialized")

        import json as _json
        chat_type = self._chat_types.get(chat_id, "")
        is_group = chat_type == "group"
        url = (
            f"https://{_DINGTALK_API_HOST}/v1.0/robot/groupMessages/send"
            if is_group
            else f"https://{_DINGTALK_API_HOST}/v1.0/robot/oToMessages/batchSend"
        )

        payload: Dict[str, Any] = {
            "robotCode": self._client_id,
            "msgKey": msg_key,
            "msgParam": _json.dumps(extra or {}),
        }
        if is_group:
            payload["openConversationId"] = chat_id
        else:
            sender_id = self._chat_senders.get(chat_id, chat_id)
            payload["userIds"] = [sender_id]

        try:
            resp = await self._http_client.post(
                url,
                headers={
                    "x-acs-dingtalk-access-token": self._access_token,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=15.0,
            )
            if resp.status_code < 300:
                data = resp.json()
                errcode = data.get("errcode", 0)
                if errcode == 0:
                    logger.info("[%s] Proactive media sent: %s to %s", self.name, msg_key, chat_id)
                    return SendResult(success=True, message_id=data.get("processQueryKey", ""))
                logger.warning("[%s] Proactive media error: %s", self.name, data)
                return SendResult(success=False, error=f"DingTalk error: {data}")
            logger.warning("[%s] Proactive media HTTP %d: %s", self.name, resp.status_code, resp.text[:200])
            return SendResult(success=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error("[%s] Proactive media exception: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def _get_audio_duration_ms(self, file_path: str) -> int:
        """Estimate audio duration in ms via ffprobe, fallback 1000."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                "-of", "csv=p=0", file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return int(float(stdout.decode().strip()) * 1000)
        except (FileNotFoundError, ValueError):
            pass
        return 1000

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """Upload file and send as markdown link."""
        display_name = file_name or os.path.basename(file_path)
        media_id = await self._upload_media(file_path, "file")
        if media_id:
            content = f"[📎 {display_name}]({media_id})"
            if caption:
                content = f"{caption}\n\n{content}"
            return await self.send(chat_id, content, reply_to=reply_to)
        # Fallback
        text = f"📎 {display_name}"
        if caption:
            text = f"{caption}\n{text}"
        return await self.send(chat_id, text, reply_to=reply_to)

    # Max upload sizes per DingTalk API (bytes)
    _UPLOAD_SIZE_LIMITS = {
        "image": 20 * 1024 * 1024,   # 20 MB
        "voice": 2 * 1024 * 1024,    # 2 MB
        "file": 20 * 1024 * 1024,    # 20 MB
        "video": 20 * 1024 * 1024,   # 20 MB
    }

    async def _upload_media(self, file_path: str, media_type: str) -> Optional[str]:
        """Upload media to DingTalk server, return media_id."""
        if not await self._refresh_access_token():
            return None
        if not self._http_client:
            return None

        # Validate file size
        file_size = os.path.getsize(file_path)
        max_size = self._UPLOAD_SIZE_LIMITS.get(media_type, 20 * 1024 * 1024)
        if file_size > max_size:
            logger.warning(
                "[%s] File too large for upload: %s (%d bytes, limit %d for %s)",
                self.name, file_path, file_size, max_size, media_type,
            )
            return None

        try:
            url = f"https://oapi.dingtalk.com/media/upload"
            params = {"access_token": self._access_token, "type": media_type}

            with open(file_path, "rb") as f:
                file_data = f.read()

            file_name = os.path.basename(file_path)
            files = {"media": (file_name, file_data)}

            resp = await self._http_client.post(url, params=params, files=files)
            result = resp.json()
            if result.get("errcode") == 0:
                return result.get("media_id")
            logger.error("[%s] Media upload failed: %s", self.name, result)
            return None
        except Exception as e:
            logger.error("[%s] Media upload error: %s", self.name, e)
            return None

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        """Edit a previously sent message.

        AI Cards can be updated in-place via streaming API (active) or
        updateCardVariables (finalized). Regular markdown messages fall back
        to sending a new message with edit indicator.
        """
        # Try AI Card edit if message_id is a tracked outTrackId
        track_info = self._sent_card_tracks.get(message_id)
        if track_info:
            target_chat_id, expire = track_info
            if time.time() > expire:
                self._sent_card_tracks.pop(message_id, None)
            else:
                # Check if card is still streaming (active)
                card_info = self._ai_card_instances.get(target_chat_id)
                if card_info and card_info["out_track_id"] == message_id:
                    # Active card — use streaming API
                    ok = await self.ai_card_stream_update(target_chat_id, content, is_final=True)
                    if ok:
                        return SendResult(success=True, message_id=message_id)
                # Finalized card — use updateCardVariables API
                if self._http_client and await self._refresh_access_token():
                    try:
                        url = f"https://{_DINGTALK_API_HOST}/v1.0/card/instances"
                        resp = await self._http_client.put(
                            url,
                            headers={
                                "x-acs-dingtalk-access-token": self._access_token or "",
                                "Content-Type": "application/json",
                            },
                            json={
                                "outTrackId": message_id,
                                "cardData": {
                                    "cardParamMap": {"markdownTextiA9sAH": content[:self.MAX_MESSAGE_LENGTH]},
                                },
                                "cardUpdateOptions": {"updateCardDataByKey": True},
                            },
                            timeout=10.0,
                        )
                        if resp.status_code < 300:
                            return SendResult(success=True, message_id=message_id)
                        logger.debug(
                            "[%s] updateCardVariables failed HTTP %d, falling back",
                            self.name, resp.status_code,
                        )
                    except Exception as e:
                        logger.debug("[%s] updateCardVariables error: %s, falling back", self.name, e)

        # Fallback: send new message with edit marker
        content = f"*(编辑)*\n\n{content}"
        return await self.send(chat_id, content)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """DingTalk does not support typing indicators."""
        pass

    # ================================================================
    # AI Card streaming
    # ================================================================

    async def _send_via_ai_card(
        self, chat_id: str, content: str, metadata: Dict[str, Any],
    ) -> SendResult:
        """Send message as an AI Card via createAndDeliver API."""
        if not self._http_client or not await self._refresh_access_token():
            return SendResult(success=False, error="No HTTP client or token")

        try:
            card_instance_id = f"card_{uuid.uuid4().hex}"
            is_group = self._chat_types.get(chat_id) == "group"

            # For DM, openSpaceId needs userId, not cid-prefixed conversationId
            # (matching TS plugin: const to = isDirect ? senderId : groupId)
            if is_group:
                space_id = chat_id
            else:
                space_id = self._chat_senders.get(chat_id, chat_id)

            # Build createAndDeliver payload (matching TS plugin)
            body: Dict[str, Any] = {
                "cardTemplateId": _CARD_TEMPLATE_ID,
                "outTrackId": card_instance_id,
                "cardData": {
                    "cardParamMap": {
                        "config": '{"autoLayout":true,"enableForward":true}',
                        _CARD_CONTENT_KEY: content or "",
                        "stop_action": "true",
                    },
                },
                "callbackType": "STREAM",
                "imGroupOpenSpaceModel": {"supportForward": True},
                "imRobotOpenSpaceModel": {"supportForward": True},
                "openSpaceId": (
                    f"dtv1.card//IM_GROUP.{space_id}" if is_group
                    else f"dtv1.card//IM_ROBOT.{space_id}"
                ),
                "userIdType": 1,
            }
            if is_group:
                body["imGroupOpenDeliverModel"] = {
                    "robotCode": self._client_id,
                    "extension": {"dynamicSummary": "true"},
                }
            else:
                body["imRobotOpenDeliverModel"] = {
                    "spaceType": "IM_ROBOT",
                    "robotCode": self._client_id,
                    "extension": {"dynamicSummary": "true"},
                }

            create_url = f"https://{_DINGTALK_API_HOST}/v1.0/card/instances/createAndDeliver"
            resp = await self._http_client.post(
                create_url,
                headers={
                    "x-acs-dingtalk-access-token": self._access_token or "",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=15.0,
            )

            resp_body = resp.text[:500] if resp.text else ""
            if resp.status_code >= 300:
                logger.warning("[%s] AI Card create failed: HTTP %d %s", self.name, resp.status_code, resp_body)
                return SendResult(success=False, error=f"Card create failed: {resp.status_code}")

            # Check errcode and deliverResults in response
            try:
                resp_json = resp.json() if resp.text else {}
                errcode = resp_json.get("errcode", 0)
                if errcode != 0:
                    logger.warning("[%s] AI Card create errcode=%d errmsg=%s", self.name, errcode, resp_json.get("errmsg", ""))
                    return SendResult(success=False, error=f"Card create errcode: {errcode}")
                # Check deliverResults for per-delivery errors
                result = resp_json.get("result", {})
                deliver_results = result.get("deliverResults", [])
                for dr in deliver_results:
                    if not dr.get("success", True):
                        err_msg = dr.get("errorMsg", "unknown")
                        logger.warning(
                            "[%s] AI Card deliver failed: spaceType=%s spaceId=%s error=%s",
                            self.name, dr.get("spaceType"), dr.get("spaceId", "")[:30], err_msg,
                        )
                        return SendResult(success=False, error=f"Card deliver failed: {err_msg}")
                # Use API-returned outTrackId if available
                api_out_track_id = result.get("outTrackId") or resp_json.get("outTrackId")
                if api_out_track_id and isinstance(api_out_track_id, str) and api_out_track_id.strip():
                    card_instance_id = api_out_track_id.strip()
                logger.info("[%s] AI Card created: track=%s resp=%s", self.name, card_instance_id[:20], resp_body[:200])
            except Exception:
                logger.info("[%s] AI Card created: track=%s (no JSON body)", self.name, card_instance_id[:20])

            # Store card instance
            self._ai_card_instances[chat_id] = {
                "out_track_id": card_instance_id,
                "created_at": time.time(),
            }
            # Track outTrackId for edit_message lookup
            self._sent_card_tracks[card_instance_id] = (chat_id, time.time() + _SENT_CARD_TTL)

            # Kick card into streaming mode (transition from PROCESSING → INPUTING)
            # This sends an empty content stream so the UI shows "输出中" immediately
            await self.ai_card_stream_update(chat_id, "", is_final=False)

            return SendResult(success=True, message_id=card_instance_id)

        except Exception as e:
            logger.error("[%s] AI Card error: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def ai_card_stream_update(self, chat_id: str, content: str, is_final: bool = False) -> bool:
        """Stream content update to an active AI Card via PUT /v1.0/card/streaming."""
        card_info = self._ai_card_instances.get(chat_id)
        if not card_info or not self._http_client:
            logger.warning("[%s] AI Card stream: no card info or HTTP client for %s", self.name, chat_id[:20])
            return False

        # Refresh token before API call (token may expire during long processing)
        if not await self._refresh_access_token():
            logger.warning("[%s] AI Card stream: token refresh failed", self.name)
            return False

        out_track_id = card_info["out_track_id"]
        content_len = len(content)
        try:
            stream_url = f"https://{_DINGTALK_API_HOST}/v1.0/card/streaming"
            resp = await self._http_client.put(
                stream_url,
                headers={
                    "x-acs-dingtalk-access-token": self._access_token or "",
                    "Content-Type": "application/json",
                },
                json={
                    "outTrackId": out_track_id,
                    "guid": str(uuid.uuid4()),
                    "key": _CARD_CONTENT_KEY,
                    "content": content[:self.MAX_MESSAGE_LENGTH],
                    "isFull": True,
                    "isFinalize": is_final,
                    "isError": False,
                },
                timeout=30.0,
            )

            resp_body = resp.text[:500] if resp.text else ""
            if resp.status_code >= 300:
                logger.warning(
                    "[%s] AI Card stream failed: HTTP %d body=%s",
                    self.name, resp.status_code, resp_body,
                )
                return False

            # Check DingTalk errcode in response body
            try:
                resp_json = resp.json() if resp.text else {}
                errcode = resp_json.get("errcode", 0)
                if errcode != 0:
                    logger.warning(
                        "[%s] AI Card stream errcode=%d errmsg=%s",
                        self.name, errcode, resp_json.get("errmsg", ""),
                    )
                    return False
            except Exception:
                pass

            logger.info(
                "[%s] AI Card stream OK: %d chars, final=%s, track=%s",
                self.name, content_len, is_final, out_track_id[:20],
            )

            if is_final:
                self._ai_card_instances.pop(chat_id, None)

            return True

        except Exception as e:
            logger.error("[%s] AI Card stream error: %s", self.name, e)
            return False

    async def ai_card_stop(self, chat_id: str) -> bool:
        """Stop an active AI Card (mark as stopped via streaming API)."""
        card_info = self._ai_card_instances.get(chat_id)
        if not card_info:
            return False

        out_track_id = card_info["out_track_id"]
        try:
            if self._http_client and self._access_token:
                stream_url = f"https://{_DINGTALK_API_HOST}/v1.0/card/streaming"
                await self._http_client.put(
                    stream_url,
                    headers={
                        "x-acs-dingtalk-access-token": self._access_token,
                        "Content-Type": "application/json",
                    },
                    json={
                        "outTrackId": out_track_id,
                        "guid": uuid.uuid4().hex,
                        "key": _CARD_CONTENT_KEY,
                        "content": "⏹️ 已停止",
                        "isFull": True,
                        "isFinalize": True,
                        "isError": True,
                    },
                    timeout=10.0,
                )
        except Exception as e:
            logger.warning("[%s] AI Card stop error: %s", self.name, e)

        self._ai_card_instances.pop(chat_id, None)
        return True

    # ================================================================
    # Interactive UI
    # ================================================================

    async def send_exec_approval(
        self,
        chat_id: str,
        command: str,
        session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send command execution approval via text keywords (ActionCard buttons not supported on DingTalk)."""
        metadata = metadata or {}
        cmd_preview = command[:800] + "..." if len(command) > 800 else command

        content = (
            f"⚠️ **需要授权执行命令**\n\n"
            f"```\n{cmd_preview}\n```\n\n"
            f"原因: {description}\n\n"
            f"请回复以下关键词：\n"
            f"- `批准` 或 `/approve` — 允许一次\n"
            f"- `/approve all` — 允许所有待执行命令\n"
            f"- `拒绝` 或 `/deny` — 拒绝执行"
        )
        return await self.send(chat_id, content, metadata=metadata)

    async def send_model_picker(
        self,
        chat_id: str,
        providers: list,
        current_model: str,
        current_provider: str,
        session_key: str,
        on_model_selected,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send model picker as ActionCard (simplified for DingTalk)."""
        metadata = metadata or {}

        content = f"**Current model:** {current_model}\n\n**Select provider:**\n\n"
        btns = []
        for p in providers[:5]:  # DingTalk ActionCard limit ~5 buttons
            name = p.get("name", p.get("slug", "?"))
            count = p.get("total_models", len(p.get("models", [])))
            label = f"{name} ({count})"
            if p.get("is_current"):
                label = f"✓ {label}"
            btns.append({
                "title": label,
                "actionURL": f"hermes://model/{session_key}/{p.get('slug', '')}",
            })

        action_card = {
            "title": "Model Picker",
            "btn_orientation": "1",
            "btns": btns,
        }
        metadata["action_card"] = action_card
        return await self.send(chat_id, content, metadata=metadata)

    # ================================================================
    # Processing lifecycle hooks
    # ================================================================

    def _reactions_enabled(self) -> bool:
        """Check if DingTalk emotion reactions are enabled."""
        return os.getenv("DINGTALK_REACTIONS", "false").lower() not in ("false", "0", "no")

    async def _emotion_call(
        self, msg_id: str, conversation_id: str,
        action: str, emotion: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Call DingTalk emotion API (reply or recall)."""
        if not self._http_client or not self._access_token:
            logger.warning("[%s] Emotion %s skipped: no http_client or access_token", self.name, action)
            return False
        emotion = emotion or _EMOTION_THINKING
        try:
            url = f"https://{_DINGTALK_API_HOST}/v1.0/robot/emotion/{action}"
            payload = {
                "robotCode": self._client_id,
                "openMsgId": msg_id,
                "openConversationId": conversation_id,
                "emotionType": 2,
                **emotion,
            }
            resp = await self._http_client.post(
                url,
                headers={
                    "x-acs-dingtalk-access-token": self._access_token,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=5.0,
            )
            if resp.status_code < 300:
                logger.info("[%s] Emotion %s succeeded for msg %s", self.name, action, msg_id)
                return True
            logger.warning(
                "[%s] Emotion %s failed: HTTP %d body=%s",
                self.name, action, resp.status_code, resp.text[:200],
            )
            return False
        except Exception as e:
            logger.warning("[%s] Emotion %s error: %s", self.name, action, e)
            return False

    async def on_processing_start(self, event: MessageEvent) -> None:
        """Attach thinking reaction and create AI Card when processing begins."""
        # Create AI Card if enabled (for streaming updates during processing)
        chat_id = event.source.chat_id
        if self._ai_card_enabled and chat_id not in self._ai_card_instances:
            result = await self._send_via_ai_card(chat_id, "", {})
            if result.success:
                logger.info("[%s] AI Card created for %s", self.name, chat_id[:20])

        if not self._reactions_enabled():
            return
        msg_id = getattr(event, "message_id", None)
        conv_id = self._msg_conversations.get(msg_id) if msg_id else None
        logger.info(
            "[%s] on_processing_start msg_id=%s conv_id=%s reactions=%s",
            self.name, msg_id, conv_id, self._reactions_enabled(),
        )
        if msg_id and conv_id:
            await self._emotion_call(msg_id, conv_id, "reply")

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        """Swap thinking reaction for completion emotion."""
        # Note: AI Card is finalized by the last send() call, not here,
        # to avoid overwriting content with empty is_final update.

        if not self._reactions_enabled():
            return
        msg_id = getattr(event, "message_id", None)
        conv_id = self._msg_conversations.get(msg_id) if msg_id else None
        logger.info(
            "[%s] on_processing_complete msg_id=%s conv_id=%s outcome=%s",
            self.name, msg_id, conv_id, outcome,
        )
        if not (msg_id and conv_id):
            return
        await self._emotion_call(msg_id, conv_id, "recall")
        if outcome != ProcessingOutcome.CANCELLED:
            await self._emotion_call(msg_id, conv_id, "reply", _EMOTION_DONE)
        self._msg_conversations.pop(msg_id, None)

    # ================================================================
    # Utilities
    # ================================================================

    async def _handle_stop_command(self, chat_id: str, sender_nick: str) -> None:
        """Handle stop command — interrupt running agent and stop AI Card."""
        handled = False

        # Stop AI Card if active
        if chat_id in self._ai_card_instances:
            await self.ai_card_stop(chat_id)
            handled = True

        # Signal interrupt to the running agent via base class session tracking
        interrupt_event = self._active_sessions.get(chat_id)
        if interrupt_event is not None:
            interrupt_event.set()
            handled = True
            logger.info("[%s] Sent interrupt signal for session %s", self.name, chat_id)

        if handled:
            await self.send(chat_id, f"⏹️ Stopped (by {sender_nick})")
            logger.info("[%s] Stop command for %s", self.name, chat_id)

    def _extract_quoted_text(self, message) -> Optional[str]:
        """Extract quoted/reply message text."""
        # Check text.repliedMsg
        text_obj = getattr(message, "text", None)
        if text_obj and hasattr(text_obj, "replied_msg"):
            replied = text_obj.replied_msg
            if replied and isinstance(replied, dict):
                content = replied.get("content", {})
                if isinstance(content, dict):
                    return content.get("text", "")
                return str(content) if content else None

        # Check quoteMessage
        quote_msg = getattr(message, "quote_message", None)
        if quote_msg:
            if hasattr(quote_msg, "text"):
                qt = quote_msg.text
                if hasattr(qt, "content"):
                    return str(qt.content)
                return str(qt)
            return str(quote_msg)

        # Check content.quoteContent
        raw_content = getattr(message, "content", None)
        if raw_content and isinstance(raw_content, dict):
            qc = raw_content.get("quoteContent")
            if qc:
                return str(qc)

        return None

    def _clean_bot_trigger_text(self, text: str) -> str:
        """Remove @bot mentions from message text."""
        text = self._mention_pattern.sub('', text)
        return ' '.join(text.split()).strip()

    def _save_temp_file(self, data: bytes, suffix: str = ".dat") -> Optional[str]:
        """Save data to a temp file, return path. Caller is responsible for cleanup."""
        try:
            import tempfile
            fd, path = tempfile.mkstemp(suffix=suffix)
            os.write(fd, data)
            os.close(fd)
            return path
        except Exception as e:
            logger.error("[%s] Failed to save temp file: %s", self.name, e)
            return None

    def _parse_timestamp(self, create_at) -> datetime:
        """Parse DingTalk create_at timestamp."""
        try:
            if create_at:
                ts = int(create_at) / 1000
                return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError, TypeError):
            pass
        return datetime.now(tz=timezone.utc)

    def format_message(self, content: str) -> str:
        """Format message for DingTalk markdown (limited subset)."""
        if not content:
            return ""

        # Strip unsupported markdown syntax
        # DingTalk supports: # headers, **bold**, [link](url), ![img](url), ```code```, - lists
        # Remove: ~~strikethrough__, ||spoiler||, _italic_
        content = re.sub(r'~~([^~]+)~~', r'\1', content)  # strikethrough
        content = re.sub(r'\|\|([^|]+)\|\|', r'\1', content)  # spoiler

        # Truncate
        if len(content) > self.MAX_MESSAGE_LENGTH:
            content = content[:self.MAX_MESSAGE_LENGTH - 3] + "..."

        return content

    def _build_action_card(self, action_card: Dict, content: str) -> Dict:
        """Build ActionCard message payload."""
        return {
            "msgtype": "action_card",
            "action_card": {
                "title": action_card.get("title", "Hermes"),
                "markdown": content,
                "btn_orientation": action_card.get("btn_orientation", "0"),
                "btns": action_card.get("btns", []),
            },
        }

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return basic info about a DingTalk conversation."""
        return {
            "name": chat_id,
            "type": "group" if "group" in chat_id.lower() else "dm",
            "webhook_available": chat_id in self._session_webhooks,
        }


# ---------------------------------------------------------------------------
# Internal stream handler
# ---------------------------------------------------------------------------

class _IncomingHandler(ChatbotHandler if DINGTALK_STREAM_AVAILABLE else object):
    """dingtalk-stream ChatbotHandler that forwards messages to the adapter."""

    def __init__(self, adapter: DingTalkAdapter):
        if DINGTALK_STREAM_AVAILABLE:
            super().__init__()
        self._adapter = adapter

    async def process(self, message):
        """Called by dingtalk-stream when a message arrives."""
        try:
            if hasattr(message, 'data') and message.data:
                chatbot_msg = dingtalk_stream.ChatbotMessage.from_dict(message.data)

                # Ensure session_webhook is set from raw data
                if not getattr(chatbot_msg, 'session_webhook', None):
                    raw_webhook = message.data.get('sessionWebhook', '')
                    if raw_webhook:
                        chatbot_msg.session_webhook = raw_webhook

                # Ensure content field is set (SDK may not parse it for media messages)
                if not getattr(chatbot_msg, 'content', None) and 'content' in message.data:
                    chatbot_msg.content = message.data['content']

                # Debug: log raw data keys and msgtype
                msgtype = message.data.get('msgtype', '')
                if msgtype != 'text':
                    logger.info("[DingTalk] Raw msgtype=%s data keys: %s", msgtype, list(message.data.keys()))
                    if 'content' in message.data:
                        logger.info("[DingTalk] Raw content: %s", message.data['content'])

                # Set quoteMessage if present
                if 'quoteMessage' in message.data:
                    chatbot_msg.quote_message = message.data['quoteMessage']

                # Set atUsers if present
                if 'atUsers' in message.data:
                    chatbot_msg.at_users = message.data['atUsers']

                await self._adapter._on_message(chatbot_msg)
            else:
                logger.warning("[%s] No data in CallbackMessage", self._adapter.name)
        except Exception:
            logger.exception("[%s] Error processing incoming message", self._adapter.name)

        return dingtalk_stream.AckMessage.STATUS_OK, "OK"
