"""
WebSocket consumers for the Voice Activator server.

HardwareConsumer — accepts edge device connections, pipes audio to ASR,
                   broadcasts results to dashboard group.

DashboardConsumer — accepts UI connections, pushes real-time events,
                    handles keyword change commands.
"""

import asyncio
import json
import logging
import time

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .asr_client import ASRClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (single-device prototype)
# ---------------------------------------------------------------------------
# Holds a reference to the currently connected HardwareConsumer instance.
# For multi-device support, replace with a dict keyed by device_id.
_hardware_consumer: "HardwareConsumer | None" = None
_current_keyword: str = getattr(settings, "DEFAULT_KEYWORD", "activate")
_server_start_time: float = time.time()


def get_hardware_consumer() -> "HardwareConsumer | None":
    """Get the currently connected hardware consumer (if any)."""
    return _hardware_consumer


def get_current_keyword() -> str:
    """Get the current active keyword."""
    return _current_keyword


def get_uptime() -> float:
    """Get server uptime in seconds."""
    return time.time() - _server_start_time


# ---------------------------------------------------------------------------
# Hardware Consumer
# ---------------------------------------------------------------------------
class HardwareConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for the edge hardware device.

    Accepts binary PCM audio and forwards it to the ASR server.
    Accepts JSON control messages (keyword_detected, stream_end).
    Sends JSON commands to hardware (set_keyword).
    """

    DASHBOARD_GROUP = "dashboard_updates"

    async def connect(self):
        global _hardware_consumer

        # Only allow one hardware device at a time (prototype)
        if _hardware_consumer is not None:
            logger.warning("Rejecting hardware connection: device already connected")
            await self.close(code=4001)
            return

        await self.accept()
        _hardware_consumer = self
        self._bytes_received = 0
        self._chunks_received = 0
        self._connected_at = time.time()
        self._is_streaming = False

        # Join the dashboard broadcast group
        await self.channel_layer.group_add(self.DASHBOARD_GROUP, self.channel_name)

        # Initialize ASR client
        self.asr_client = ASRClient()
        asr_connected = await self.asr_client.connect()

        if asr_connected:
            # Start listening for ASR results in background
            await self.asr_client.start_listening(self._on_asr_result)
            logger.info("Hardware connected — ASR pipeline ready")
        else:
            logger.warning(
                "Hardware connected but ASR server unavailable at %s",
                settings.ASR_SERVER_URL,
            )

        # Broadcast device connected event to dashboard
        await self.channel_layer.group_send(
            self.DASHBOARD_GROUP,
            {
                "type": "device.status",
                "status": "connected",
                "device_info": {
                    "device_id": "hardware_0",
                    "ip": self.scope.get("client", ["unknown", 0])[0],
                },
                "asr_connected": asr_connected,
                "timestamp": time.time(),
            },
        )

    async def disconnect(self, close_code):
        global _hardware_consumer

        # Clean up ASR connection
        if hasattr(self, "asr_client"):
            await self.asr_client.disconnect()

        # Broadcast device disconnected
        await self.channel_layer.group_send(
            self.DASHBOARD_GROUP,
            {
                "type": "device.status",
                "status": "disconnected",
                "device_info": None,
                "asr_connected": False,
                "timestamp": time.time(),
            },
        )

        # Leave group and clear reference
        await self.channel_layer.group_discard(self.DASHBOARD_GROUP, self.channel_name)
        _hardware_consumer = None
        logger.info(
            "Hardware disconnected (code=%s, bytes=%d, chunks=%d)",
            close_code,
            self._bytes_received,
            self._chunks_received,
        )

    async def receive(self, text_data=None, bytes_data=None):
        """
        Handle incoming data from the hardware device.
        - Binary data: raw PCM audio → forward to ASR
        - Text data: JSON control messages
        """
        if bytes_data:
            await self._handle_audio(bytes_data)
        elif text_data:
            await self._handle_json(text_data)

    async def _handle_audio(self, pcm_chunk: bytes) -> None:
        """Forward raw PCM audio chunk to the ASR server with zero delay."""
        self._bytes_received += len(pcm_chunk)
        self._chunks_received += 1

        if not self.asr_client.is_connected:
            # Try to reconnect
            logger.warning("ASR not connected, attempting reconnect...")
            success = await self.asr_client.reconnect(self._on_asr_result)
            if not success:
                # Notify dashboard about ASR issues
                await self.channel_layer.group_send(
                    self.DASHBOARD_GROUP,
                    {
                        "type": "error.event",
                        "code": "ASR_DISCONNECTED",
                        "message": "Cannot forward audio: ASR server unavailable",
                        "timestamp": time.time(),
                    },
                )
                return

        # Direct pipe — zero buffering for minimal latency
        await self.asr_client.send_audio(pcm_chunk)

    async def _handle_json(self, text_data: str) -> None:
        """Handle JSON control messages from hardware."""
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from hardware: %s", text_data[:100])
            return

        msg_type = data.get("type")

        if msg_type == "keyword_detected":
            self._is_streaming = True
            logger.info(
                "Keyword detected: '%s' (confidence: %.2f)",
                data.get("keyword", "?"),
                data.get("confidence", 0),
            )
            # Broadcast keyword detection to dashboard
            await self.channel_layer.group_send(
                self.DASHBOARD_GROUP,
                {
                    "type": "keyword.detected",
                    "keyword": data.get("keyword", ""),
                    "confidence": data.get("confidence", 0.0),
                    "timestamp": data.get("timestamp", time.time()),
                },
            )

        elif msg_type == "stream_end":
            self._is_streaming = False
            logger.info("Audio stream ended: %s", data.get("reason", "unknown"))

            # Signal ASR that the stream ended
            if self.asr_client.is_connected:
                await self.asr_client.signal_end_of_stream()

            # Broadcast stream end to dashboard
            await self.channel_layer.group_send(
                self.DASHBOARD_GROUP,
                {
                    "type": "stream.end",
                    "reason": data.get("reason", "unknown"),
                    "session_duration_ms": data.get("session_duration_ms", 0),
                    "timestamp": time.time(),
                },
            )

        elif msg_type == "keyword_change_ack":
            # Hardware confirms keyword change
            global _current_keyword
            new_kw = data.get("keyword", "")
            old_kw = _current_keyword
            _current_keyword = new_kw
            logger.info("Hardware confirmed keyword change: '%s' → '%s'", old_kw, new_kw)
            await self.channel_layer.group_send(
                self.DASHBOARD_GROUP,
                {
                    "type": "keyword.changed",
                    "old_keyword": old_kw,
                    "new_keyword": new_kw,
                    "success": True,
                    "timestamp": time.time(),
                },
            )

        else:
            logger.debug("Unknown message type from hardware: %s", msg_type)

    async def send_to_hardware(self, message: dict) -> None:
        """Send a JSON command to the connected hardware device."""
        await self.send(text_data=json.dumps(message))

    # -----------------------------------------------------------------------
    # Group message handlers (no-op on hardware side)
    # -----------------------------------------------------------------------
    # HardwareConsumer is a member of DASHBOARD_GROUP so it receives its own
    # broadcasts. These handlers silently discard messages not meant for the
    # hardware device.

    async def device_status(self, event: dict) -> None:
        pass

    async def keyword_detected(self, event: dict) -> None:
        pass

    async def keyword_changed(self, event: dict) -> None:
        pass

    async def stream_end(self, event: dict) -> None:
        pass

    async def transcription_event(self, event: dict) -> None:
        pass

    async def error_event(self, event: dict) -> None:
        pass

    async def _on_asr_result(self, result: dict) -> None:
        """
        Callback invoked by ASRClient when a transcription result arrives.
        Broadcasts the result to all dashboard consumers.
        """
        result_type = result.get("type")

        if result_type == "transcription":
            text = result.get("text", "")
            # Only broadcast if there's actual text content
            if text:
                await self.channel_layer.group_send(
                    self.DASHBOARD_GROUP,
                    {
                        "type": "transcription.event",
                        "text": text,
                        "is_partial": result.get("is_partial", True),
                        "segment_id": result.get("segment_id", 0),
                        "timestamp": result.get("timestamp", time.time()),
                    },
                )
        elif result_type == "error":
            await self.channel_layer.group_send(
                self.DASHBOARD_GROUP,
                {
                    "type": "error.event",
                    "code": result.get("code", "ASR_ERROR"),
                    "message": result.get("message", "Unknown ASR error"),
                    "timestamp": result.get("timestamp", time.time()),
                },
            )


# ---------------------------------------------------------------------------
# Dashboard Consumer
# ---------------------------------------------------------------------------
class DashboardConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for the UI dashboard.

    Receives real-time events (transcriptions, device status, keyword detection)
    via channel layer group messages. Handles UI commands (change_keyword, get_status).
    """

    DASHBOARD_GROUP = "dashboard_updates"

    async def connect(self):
        await self.accept()
        await self.channel_layer.group_add(self.DASHBOARD_GROUP, self.channel_name)
        logger.info("Dashboard client connected")

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.DASHBOARD_GROUP, self.channel_name)
        logger.info("Dashboard client disconnected (code=%s)", close_code)

    async def receive(self, text_data=None, bytes_data=None):
        """Handle commands from the UI."""
        if not text_data:
            return

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self._send_error("INVALID_MESSAGE", "Invalid JSON format")
            return

        msg_type = data.get("type")

        if msg_type == "change_keyword":
            await self._handle_change_keyword(data)
        elif msg_type == "get_status":
            await self._handle_get_status()
        else:
            await self._send_error(
                "INVALID_MESSAGE", f"Unknown command type: {msg_type}"
            )

    # -----------------------------------------------------------------------
    # Command handlers
    # -----------------------------------------------------------------------

    async def _handle_change_keyword(self, data: dict) -> None:
        """Forward keyword change to hardware device."""
        global _current_keyword

        new_keyword = data.get("keyword", "").strip()
        if not new_keyword:
            await self._send_error("INVALID_MESSAGE", "Keyword cannot be empty")
            return
        if len(new_keyword) > 50:
            await self._send_error("INVALID_MESSAGE", "Keyword too long (max 50 chars)")
            return

        hw = get_hardware_consumer()
        if hw is None:
            await self._send_error(
                "DEVICE_NOT_CONNECTED", "No hardware device connected"
            )
            return

        old_keyword = _current_keyword

        # Send keyword change command to hardware
        try:
            await hw.send_to_hardware(
                {"type": "set_keyword", "keyword": new_keyword}
            )
            logger.info("Sent keyword change to hardware: '%s' → '%s'", old_keyword, new_keyword)

            # Optimistically update — hardware will confirm via keyword_change_ack
            # For the prototype, we update immediately since hardware may not ack
            _current_keyword = new_keyword
            await self.channel_layer.group_send(
                self.DASHBOARD_GROUP,
                {
                    "type": "keyword.changed",
                    "old_keyword": old_keyword,
                    "new_keyword": new_keyword,
                    "success": True,
                    "timestamp": time.time(),
                },
            )
        except Exception as e:
            logger.error("Failed to send keyword change to hardware: %s", e)
            await self._send_error(
                "KEYWORD_CHANGE_FAILED",
                f"Failed to communicate with hardware: {e}",
            )

    async def _handle_get_status(self) -> None:
        """Reply with current system status."""
        hw = get_hardware_consumer()
        asr_connected = False
        if hw and hasattr(hw, "asr_client"):
            asr_connected = hw.asr_client.is_connected

        await self.send(
            text_data=json.dumps(
                {
                    "type": "status_response",
                    "device_connected": hw is not None,
                    "current_keyword": get_current_keyword(),
                    "asr_connected": asr_connected,
                    "uptime_seconds": round(get_uptime(), 1),
                    "timestamp": time.time(),
                }
            )
        )

    # -----------------------------------------------------------------------
    # Group message handlers (channel layer events → WebSocket sends)
    # -----------------------------------------------------------------------
    # Method names correspond to the "type" field in group_send calls,
    # with dots replaced by underscores (Django Channels convention).

    async def transcription_event(self, event: dict) -> None:
        """Forward transcription result to the UI."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "transcription",
                    "text": event["text"],
                    "is_partial": event["is_partial"],
                    "segment_id": event["segment_id"],
                    "timestamp": event["timestamp"],
                }
            )
        )

    async def keyword_detected(self, event: dict) -> None:
        """Forward keyword detection event to the UI."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "keyword_detected",
                    "keyword": event["keyword"],
                    "confidence": event["confidence"],
                    "timestamp": event["timestamp"],
                }
            )
        )

    async def device_status(self, event: dict) -> None:
        """Forward device connection status to the UI."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "device_status",
                    "status": event["status"],
                    "device_info": event.get("device_info"),
                    "timestamp": event["timestamp"],
                }
            )
        )

    async def stream_end(self, event: dict) -> None:
        """Forward stream-end event to the UI."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "stream_end",
                    "reason": event["reason"],
                    "session_duration_ms": event.get("session_duration_ms", 0),
                    "timestamp": event["timestamp"],
                }
            )
        )

    async def keyword_changed(self, event: dict) -> None:
        """Forward keyword change confirmation to the UI."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "keyword_changed",
                    "old_keyword": event["old_keyword"],
                    "new_keyword": event["new_keyword"],
                    "success": event["success"],
                    "timestamp": event["timestamp"],
                }
            )
        )

    async def error_event(self, event: dict) -> None:
        """Forward error event to the UI."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "error",
                    "code": event["code"],
                    "message": event["message"],
                    "timestamp": event["timestamp"],
                }
            )
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    async def _send_error(self, code: str, message: str) -> None:
        """Send an error message directly to this WebSocket client."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "error",
                    "code": code,
                    "message": message,
                    "timestamp": time.time(),
                }
            )
        )
