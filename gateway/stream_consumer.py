"""Gateway streaming consumer — bridges sync agent callbacks to async platform delivery.

The agent fires stream_delta_callback(text) synchronously from its worker thread.
GatewayStreamConsumer:
  1. Receives deltas via on_delta() (thread-safe, sync)
  2. Queues them to an asyncio task via queue.Queue
  3. The async run() task buffers, rate-limits, and progressively edits
     a single message on the target platform

Design: Uses the edit transport (send initial message, then editMessageText).
This is universally supported across Telegram, Discord, and Slack.

Credit: jobless0x (#774, #1312), OutThisLife (#798), clicksingh (#697).
"""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("gateway.stream_consumer")

# Sentinel to signal the stream is complete
_DONE = object()


@dataclass
class StreamConsumerConfig:
    """Runtime config for a single stream consumer instance."""
    edit_interval: float = 0.3
    buffer_threshold: int = 40
    cursor: str = " ▉"


class GatewayStreamConsumer:
    """Async consumer that progressively edits a platform message with streamed tokens.

    Usage::

        consumer = GatewayStreamConsumer(adapter, chat_id, config, metadata=metadata)
        # Pass consumer.on_delta as stream_delta_callback to AIAgent
        agent = AIAgent(..., stream_delta_callback=consumer.on_delta)
        # Start the consumer as an asyncio task
        task = asyncio.create_task(consumer.run())
        # ... run agent in thread pool ...
        consumer.finish()  # signal completion
        await task         # wait for final edit
    """

    def __init__(
        self,
        adapter: Any,
        chat_id: str,
        config: Optional[StreamConsumerConfig] = None,
        metadata: Optional[dict] = None,
    ):
        self.adapter = adapter
        self.chat_id = chat_id
        self.cfg = config or StreamConsumerConfig()
        self.metadata = metadata
        self._queue: queue.Queue = queue.Queue()
        self._accumulated = ""
        self._message_id: Optional[str] = None
        self._already_sent = False
        self._edit_supported = True  # Disabled on first edit failure (Signal/Email/HA)
        self._last_edit_time = 0.0
        self._last_sent_text = ""   # Track last-sent text to skip redundant edits
        self._failed_text: Optional[str] = None  # Text that couldn't be delivered mid-stream

    @property
    def already_sent(self) -> bool:
        """True if at least one message was sent/edited — signals the base
        adapter to skip re-sending the final response."""
        return self._already_sent

    @property
    def has_undelivered_text(self) -> bool:
        """True if there is text that couldn't be delivered during streaming.
        The caller should retrieve this via get_remaining_text() and send it
        via the normal (non-streaming) path."""
        return self._failed_text is not None and len(self._failed_text) > 0

    def get_remaining_text(self) -> Optional[str]:
        """Get text that couldn't be delivered during streaming.
        Returns None if all text was delivered successfully."""
        return self._failed_text

    def on_delta(self, text: str) -> None:
        """Thread-safe callback — called from the agent's worker thread."""
        if text:
            self._queue.put(text)

    def finish(self) -> None:
        """Signal that the stream is complete."""
        self._queue.put(_DONE)

    async def run(self) -> None:
        """Async task that drains the queue and edits the platform message."""
        # Platform message length limit — leave room for cursor + formatting
        _raw_limit = getattr(self.adapter, "MAX_MESSAGE_LENGTH", 4096)
        _safe_limit = max(500, _raw_limit - len(self.cfg.cursor) - 100)

        try:
            while True:
                # Drain all available items from the queue
                got_done = False
                while True:
                    try:
                        item = self._queue.get_nowait()
                        if item is _DONE:
                            got_done = True
                            break
                        self._accumulated += item
                    except queue.Empty:
                        break

                # Decide whether to flush an edit
                now = time.monotonic()
                elapsed = now - self._last_edit_time
                should_edit = (
                    got_done
                    or (elapsed >= self.cfg.edit_interval
                        and len(self._accumulated) > 0)
                    or len(self._accumulated) >= self.cfg.buffer_threshold
                )

                if should_edit and self._accumulated:
                    # Split overflow: if accumulated text exceeds the platform
                    # limit, finalize the current message and start a new one.
                    overflow_send_failed = False
                    while (
                        len(self._accumulated) > _safe_limit
                        and self._message_id is not None
                        and not overflow_send_failed
                    ):
                        split_at = self._accumulated.rfind("\n", 0, _safe_limit)
                        if split_at < _safe_limit // 2:
                            split_at = _safe_limit
                        chunk = self._accumulated[:split_at]
                        success = await self._send_or_edit(chunk)
                        if success:
                            self._accumulated = self._accumulated[split_at:].lstrip("\n")
                            self._message_id = None
                            self._last_sent_text = ""
                        else:
                            # Send failed - stop splitting; remaining text
                            # will be captured when stream ends or next iteration
                            overflow_send_failed = True
                            logger.warning(
                                "Overflow send failed in thread/chat %s. "
                                "Remaining %d chars queued for fallback.",
                                self.chat_id, len(self._accumulated)
                            )

                    # Only send more if we haven't hit a failure
                    if not overflow_send_failed:
                        display_text = self._accumulated
                        if not got_done:
                            display_text += self.cfg.cursor

                        await self._send_or_edit(display_text)
                        self._last_edit_time = time.monotonic()

                if got_done:
                    # Handle overflow before final edit: if accumulated text exceeds
                    # the safe limit, split and send as new messages.
                    if self._accumulated and self._message_id:
                        while len(self._accumulated) > _safe_limit:
                            split_at = self._accumulated.rfind("\n", 0, _safe_limit)
                            if split_at < _safe_limit // 2:
                                split_at = _safe_limit
                            chunk = self._accumulated[:split_at]
                            success = await self._send_or_edit(chunk)
                            if success:
                                self._accumulated = self._accumulated[split_at:].lstrip("\n")
                                self._message_id = None
                                self._last_sent_text = ""
                            else:
                                # Send failed - capture remaining for fallback
                                logger.warning(
                                    "Final overflow send failed in thread/chat %s. "
                                    "Remaining %d chars queued for fallback.",
                                    self.chat_id, len(self._accumulated)
                                )
                                if self._failed_text is None:
                                    self._failed_text = self._accumulated
                                else:
                                    self._failed_text += "\n" + self._accumulated
                                self._accumulated = ""
                                break
                        # Final edit for remaining content (within limits)
                        if self._accumulated and self._message_id:
                            await self._send_or_edit(self._accumulated)
                    # If we have accumulated text but no message_id, send failed
                    # mid-stream. Capture remaining text for fallback delivery.
                    if self._accumulated and not self._message_id:
                        logger.warning(
                            "Stream ended with %d chars undelivered in thread/chat %s "
                            "(no active message). Queuing for fallback delivery.",
                            len(self._accumulated), self.chat_id
                        )
                        if self._failed_text is None:
                            self._failed_text = self._accumulated
                        else:
                            self._failed_text += "\n" + self._accumulated
                    return

                await asyncio.sleep(0.05)  # Small yield to not busy-loop

        except asyncio.CancelledError:
            # Best-effort final edit on cancellation
            if self._accumulated and self._message_id:
                try:
                    await self._send_or_edit(self._accumulated)
                except Exception:
                    pass
        except Exception as e:
            logger.error("Stream consumer error: %s", e)

    async def _send_or_edit(self, text: str) -> bool:
        """Send or edit the streaming message.

        Returns True if the text was successfully delivered, False otherwise.
        On failure, the text is stored in _failed_text for fallback delivery.
        """
        try:
            if self._message_id is not None:
                if self._edit_supported:
                    # Skip if text is identical to what we last sent
                    if text == self._last_sent_text:
                        return True
                    # Edit existing message
                    result = await self.adapter.edit_message(
                        chat_id=self.chat_id,
                        message_id=self._message_id,
                        content=text,
                    )
                    if result.success:
                        self._already_sent = True
                        self._last_sent_text = text
                        return True
                    else:
                        # Edit not supported by this adapter — stop streaming,
                        # let the normal send path handle the final response.
                        # Without this guard, adapters like Signal/Email would
                        # flood the chat with a new message every edit_interval.
                        logger.debug("Edit failed, disabling streaming for this adapter")
                        self._edit_supported = False
                        # Note: for edit failures, we don't capture _failed_text
                        # because the original message exists; the final response
                        # will be sent as a new message by the caller.
                        return True
                else:
                    # Editing not supported — skip intermediate updates.
                    # The final response will be sent by the normal path.
                    return True
            else:
                # First message — send new (or follow-up after split)
                result = await self.adapter.send(
                    chat_id=self.chat_id,
                    content=text,
                    metadata=self.metadata,
                )
                if result.success and result.message_id:
                    self._message_id = result.message_id
                    self._already_sent = True
                    self._last_sent_text = text
                    return True
                else:
                    # Send failed — disable streaming and capture text for fallback
                    error_msg = getattr(result, 'error', 'unknown error')
                    logger.warning(
                        "Stream send failed in thread/chat %s: %s. "
                        "Disabling streaming and queuing %d chars for fallback delivery.",
                        self.chat_id, error_msg, len(text)
                    )
                    self._edit_supported = False
                    # Accumulate failed text for fallback delivery
                    if self._failed_text is None:
                        self._failed_text = text
                    else:
                        self._failed_text += "\n" + text
                    return False
        except Exception as e:
            logger.error("Stream send/edit error: %s", e)
            # Capture failed text on exception too
            if self._message_id is None:
                if self._failed_text is None:
                    self._failed_text = text
                else:
                    self._failed_text += "\n" + text
            return False
