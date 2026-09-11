"""
ASGI entrypoint for the Voice Activator server.

Routes:
  - HTTP  → standard Django views (REST API)
  - WS    → Django Channels consumers
    - ws/hardware/  → HardwareConsumer  (edge device audio stream)
    - ws/dashboard/ → DashboardConsumer (UI real-time updates)
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Initialize Django ASGI application early to ensure AppRegistry is populated
# before importing consumers.
django_asgi_app = get_asgi_application()

# Import routing AFTER Django setup to avoid AppRegistryNotReady
from core.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
