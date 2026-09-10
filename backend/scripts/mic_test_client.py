#!/usr/bin/env python
"""
Microphone Test Client — simulates the hardware edge device.

Connects TWO WebSockets to the Django backend:
  1. ws/hardware/ — sends mic audio + control messages (simulates edge device)
  2. ws/dashboard/ — receives live transcription results for display

Usage:
    python scripts/mic_test_client.py [--host localhost] [--port 8000]
"""

import argparse
import asyncio
import json
import sys
import time

import numpy as np
import sounddevice as sd
import websockets

# ---------------------------------------------------------------------------
# Audio settings — must match Django settings and ONNX server expectations
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000       # 16 kHz
CHANNELS = 1              # mono
BLOCK_SIZE = 1600         # 100 ms chunks (1600 samples at 16 kHz)
DTYPE = "float32"         # sounddevice captures float32, we convert to int16


# ---------------------------------------------------------------------------
# Hardware channel: send audio + handle commands from server
# ---------------------------------------------------------------------------
async def receive_hardware_messages(websocket):
    """Listen for commands from Django on the hardware channel."""
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            if msg_type == "set_keyword":
                new_kw = data.get("keyword", "")
                print(f"\n⚙  Keyword change requested: '{new_kw}'")
                ack = json.dumps({
                    "type": "keyword_change_ack",
                    "keyword": new_kw,
                    "timestamp": time.time(),
                })
                await websocket.send(ack)
                print(f"   ✓ Acknowledged keyword change to '{new_kw}'")

    except websockets.exceptions.ConnectionClosed:
        pass


async def send_mic_audio(websocket, loop):
    """Capture microphone audio and stream raw int16 PCM bytes to Django."""
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def audio_callback(indata, frames, time_info, status):
        if status:
            print(f"\n⚠  Audio status: {status}", file=sys.stderr)
        # Convert float32 [-1.0, 1.0] → int16 PCM bytes
        int16_samples = (indata[:, 0] * 32767).astype(np.int16)
        loop.call_soon_threadsafe(queue.put_nowait, int16_samples.tobytes())

    with sd.InputStream(
        channels=CHANNELS,
        samplerate=SAMPLE_RATE,
        dtype=DTYPE,
        blocksize=BLOCK_SIZE,
        callback=audio_callback,
    ):
        while True:
            audio_bytes = await queue.get()
            await websocket.send(audio_bytes)


# ---------------------------------------------------------------------------
# Dashboard channel: receive transcription results
# ---------------------------------------------------------------------------
async def receive_transcriptions(dashboard_ws):
    """
    Listen on the dashboard WebSocket for transcription events
    and print them to the terminal.
    """
    last_text = ""
    try:
        async for message in dashboard_ws:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            if msg_type == "transcription":
                text = data.get("text", "")
                is_partial = data.get("is_partial", True)
                if text and text != last_text:
                    last_text = text
                    marker = "..." if is_partial else " ✓"
                    sys.stdout.write(f"\r\033[K[Transcription{marker}]: {text}")
                    sys.stdout.flush()
                    if not is_partial:
                        print()  # newline after final result
                        last_text = ""

            elif msg_type == "keyword_detected":
                kw = data.get("keyword", "")
                conf = data.get("confidence", 0)
                print(f"\n🔑 Keyword detected: '{kw}' (confidence: {conf:.2f})")

            elif msg_type == "device_status":
                status = data.get("status", "")
                print(f"\n📱 Device {status}")

            elif msg_type == "error":
                code = data.get("code", "")
                msg = data.get("message", "")
                print(f"\n❌ Error [{code}]: {msg}")

    except websockets.exceptions.ConnectionClosed:
        print("\n[Dashboard connection closed]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main(host: str, port: int):
    hardware_url = f"ws://{host}:{port}/ws/hardware/"
    dashboard_url = f"ws://{host}:{port}/ws/dashboard/"

    ws_opts = dict(ping_interval=20, ping_timeout=10, max_size=None)

    # --- Connect dashboard first (to catch all events) ---
    print(f"Connecting to dashboard at {dashboard_url} ...")
    try:
        dashboard_ws = await websockets.connect(dashboard_url, **ws_opts)
    except Exception as e:
        print(f"✗ Dashboard connection failed: {e}")
        sys.exit(1)
    print("✓ Dashboard connected (receiving transcriptions)\n")

    # --- Connect hardware channel ---
    print(f"Connecting to hardware channel at {hardware_url} ...")
    try:
        hardware_ws = await websockets.connect(hardware_url, **ws_opts)
    except Exception as e:
        print(f"✗ Hardware connection failed: {e}")
        await dashboard_ws.close()
        sys.exit(1)
    print("✓ Hardware connected!\n")

    # --- Send keyword_detected to trigger the ASR pipeline ---
    keyword_event = json.dumps({
        "type": "keyword_detected",
        "keyword": "activate",
        "confidence": 1.0,
        "timestamp": time.time(),
    })
    await hardware_ws.send(keyword_event)
    print("📡 Sent keyword_detected — ASR pipeline active")
    print("🎤 Microphone streaming — speak now (Ctrl+C to stop)\n")

    # --- Run everything concurrently ---
    loop = asyncio.get_running_loop()
    try:
        await asyncio.gather(
            send_mic_audio(hardware_ws, loop),
            receive_hardware_messages(hardware_ws),
            receive_transcriptions(dashboard_ws),
        )
    finally:
        await hardware_ws.close()
        await dashboard_ws.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Mic test client — simulates hardware device for Voice Activator"
    )
    parser.add_argument(
        "--host", default="localhost",
        help="Django backend host (default: localhost)",
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="Django backend port (default: 8000)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        print("\n\n🛑 Mic test client stopped.")
