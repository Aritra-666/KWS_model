"""
WebSocket URL routing for the Voice Activator server.

Routes:
  ws/hardware/  → HardwareConsumer  (edge device audio stream)
  ws/dashboard/ → DashboardConsumer (UI real-time updates)
"""

from django.urls import path

from .consumers import DashboardConsumer, HardwareConsumer

websocket_urlpatterns = [
    path("ws/hardware/", HardwareConsumer.as_asgi()),
    path("ws/dashboard/", DashboardConsumer.as_asgi()),
]
