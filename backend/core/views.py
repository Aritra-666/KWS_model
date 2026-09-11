"""
REST API views for the Voice Activator server.

Provides lightweight health and status endpoints for monitoring.
"""

import time

from django.http import JsonResponse

from .consumers import get_current_keyword, get_hardware_consumer, get_uptime


def health_check(request):
    """Simple liveness probe — returns 200 if the server is running."""
    return JsonResponse({"status": "ok", "timestamp": time.time()})


def system_status(request):
    """
    Return current system status including device connection,
    ASR connection, active keyword, and server uptime.
    """
    hw = get_hardware_consumer()
    asr_connected = False
    device_info = None

    if hw is not None:
        if hasattr(hw, "asr_client"):
            asr_connected = hw.asr_client.is_connected
        device_info = {
            "device_id": "hardware_0",
            "bytes_received": getattr(hw, "_bytes_received", 0),
            "chunks_received": getattr(hw, "_chunks_received", 0),
            "is_streaming": getattr(hw, "_is_streaming", False),
        }

    return JsonResponse(
        {
            "device_connected": hw is not None,
            "device_info": device_info,
            "asr_connected": asr_connected,
            "current_keyword": get_current_keyword(),
            "uptime_seconds": round(get_uptime(), 1),
            "timestamp": time.time(),
        }
    )
