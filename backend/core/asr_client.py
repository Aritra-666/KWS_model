"""
Async WebSocket client for the Sherpa-ONNX streaming ASR server.

Manages the WebSocket connection to the ASR server at ASR_SERVER_URL,
forwards raw PCM audio, and reads back transcription results.

This module is decoupled from Django Channels so that swapping ASR providers
in the future only requires changing this file.
"""

import asyncio
import json
import logging
import time
from typing import Callable, Optional

import websockets
from django.conf import settings

logger = logging.getLogger(__name__)


class ASRClient:
    """
    Async WebSocket client that connects to the Sherpa-ONNX streaming ASR
    server and provides methods to send audio and receive transcriptions.
    """

    def __init__(self, url: Optional[str] = None):
        self.url = url or settings.ASR_SERVER_URL
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._connected = False
        self._segment_id = 0

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None and self._ws.open

    async def connect(self) -> bool:
        """Open WebSocket connection to the ASR server."""
        try:
            self._ws = await websockets.connect(
                self.url,
                ping_interval=20,
                ping_timeout=10,
                max_size=None,  # no limit on message size
            )
            self._connected = True
            self._segment_id = 0
            logger.info("Connected to ASR server at %s", self.url)
            return True
        except Exception as e:
            self._connected = False
            logger.error("Failed to connect to ASR server at %s: %s", self.url, e)
            return False

    async def disconnect(self) -> None:
        """Gracefully close the ASR connection and cancel the listener."""
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._connected = False
        logger.info("Disconnected from ASR server")

    async def send_audio(self, pcm_chunk: bytes) -> bool:
        """
        Send a raw PCM audio chunk to the ASR server.
        Returns True if sent successfully, False otherwise.
        """
        if not self.is_connected:
            logger.warning("Cannot send audio: ASR not connected")
            return False
        try:
            await self._ws.send(pcm_chunk)
            return True
        except websockets.exceptions.ConnectionClosed:
            self._connected = False
            logger.error("ASR connection closed while sending audio")
            return False
        except Exception as e:
            logger.error("Error sending audio to ASR: %s", e)
            return False

    async def signal_end_of_stream(self) -> None:
        """
        Signal to the ASR server that the audio stream has ended.
        Different ASR servers handle this differently. For Sherpa-ONNX,
        we send an empty bytes message or a JSON control message.
        """
        if not self.is_connected:
            return
        try:
            # Send empty bytes to signal end-of-utterance
            await self._ws.send(b"")
            logger.debug("Sent end-of-stream signal to ASR")
        except Exception as e:
            logger.error("Error sending end-of-stream to ASR: %s", e)

    async def start_listening(self, callback: Callable) -> None:
        """
        Start a background task that listens for ASR responses and
        invokes the callback with each result.

        The callback receives a dict with:
            {
                "type": "transcription",
                "text": "...",
                "is_partial": True/False,
                "segment_id": int,
                "timestamp": float
            }
        """
        if self._listener_task and not self._listener_task.done():
            logger.warning("Listener already running, skipping duplicate start")
            return

        self._listener_task = asyncio.create_task(
            self._listen_loop(callback), name="asr-listener"
        )

    async def _listen_loop(self, callback: Callable) -> None:
        """Internal loop that reads ASR WebSocket messages."""
        try:
            async for message in self._ws:
                if isinstance(message, str):
                    # Sherpa-ONNX sends JSON text responses
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        # Some ASR servers send plain text
                        data = {"text": message}

                    result = self._normalize_asr_response(data)
                    await callback(result)
                elif isinstance(message, bytes):
                    # Binary response — unusual for ASR text, but handle gracefully
                    logger.debug(
                        "Received binary from ASR (%d bytes), ignoring", len(message)
                    )
        except websockets.exceptions.ConnectionClosed as e:
            self._connected = False
            logger.warning("ASR WebSocket closed: %s", e)
            # Notify about disconnection
            await callback(
                {
                    "type": "error",
                    "code": "ASR_DISCONNECTED",
                    "message": f"ASR server connection closed: {e}",
                    "timestamp": time.time(),
                }
            )
        except asyncio.CancelledError:
            logger.debug("ASR listener task cancelled")
            raise
        except Exception as e:
            logger.error("Unexpected error in ASR listener: %s", e)
            await callback(
                {
                    "type": "error",
                    "code": "ASR_DISCONNECTED",
                    "message": f"ASR listener error: {e}",
                    "timestamp": time.time(),
                }
            )

    def _normalize_asr_response(self, data: dict) -> dict:
        """
        Normalize ASR server response into our standard format.

        Sherpa-ONNX streaming server typically sends:
            {"text": "...", "segment": N, "is_final": bool}
        or sometimes just:
            {"text": "..."}

        We normalize to:
            {"type": "transcription", "text": "...", "is_partial": bool,
             "segment_id": int, "timestamp": float}
        """
        text = data.get("text", "").strip()

        # Sherpa-ONNX uses various key names for finality
        is_final = data.get("is_final", data.get("final", False))
        is_partial = not is_final

        # Segment tracking
        segment = data.get("segment", data.get("segment_id", self._segment_id))
        if is_final and text:
            self._segment_id += 1

        return {
            "type": "transcription",
            "text": text,
            "is_partial": is_partial,
            "segment_id": segment,
            "timestamp": time.time(),
        }

    async def reconnect(self, callback: Optional[Callable] = None) -> bool:
        """
        Attempt to reconnect to the ASR server.
        If a callback is provided, restarts the listener.
        """
        await self.disconnect()
        success = await self.connect()
        if success and callback:
            await self.start_listening(callback)
        return success
