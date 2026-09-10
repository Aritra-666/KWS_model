"""
Django settings for Voice Activator backend server.

This server acts as a WebSocket hub between:
- Hardware (edge device with KWS) via ws/hardware/
- Sherpa-ONNX ASR server at ASR_SERVER_URL via websockets
- UI dashboard via ws/dashboard/
"""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-insecure-key-change-in-production-hackathon-only",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = ["*"]

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "daphne",  # ASGI server — must be first for runserver override
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "corsheaders",
    "channels",
    "core",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# ASGI / Channels
# ---------------------------------------------------------------------------
ASGI_APPLICATION = "config.asgi.application"

# Redis channel layer for low-latency group messaging between consumers.
# Falls back to InMemory if REDIS_URL is not set (dev convenience).
REDIS_URL = os.environ.get("REDIS_URL", "")

if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [REDIS_URL],
                "capacity": 1500,       # max messages in channel before oldest dropped
                "expiry": 10,           # message TTL in seconds (low for real-time audio)
            },
        },
    }
else:
    # InMemory layer — works for single-process dev server (no Redis needed)
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }

# ---------------------------------------------------------------------------
# CORS — wide open for hackathon (UI may be on another laptop)
# ---------------------------------------------------------------------------
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# Database — none for now, structured for future addition
# ---------------------------------------------------------------------------
DATABASES = {}

# ---------------------------------------------------------------------------
# Static files (CSS, JavaScript, Images)
# ---------------------------------------------------------------------------
STATIC_URL = "static/"

# ---------------------------------------------------------------------------
# Voice Activator — custom settings
# ---------------------------------------------------------------------------

# The WebSocket URL of the Sherpa-ONNX streaming ASR server.
ASR_SERVER_URL = os.environ.get("ASR_SERVER_URL", "ws://localhost:6006")

# Default keyword that the hardware listens for on startup.
DEFAULT_KEYWORD = os.environ.get("DEFAULT_KEYWORD", "activate")

# Audio format expected from hardware.
AUDIO_SAMPLE_RATE = 16000   # 16 kHz
AUDIO_SAMPLE_WIDTH = 2      # 16-bit (2 bytes per sample)
AUDIO_CHANNELS = 1          # mono
AUDIO_CHUNK_MS = 20         # 20 ms per chunk
# Derived: bytes per chunk = sample_rate * sample_width * channels * chunk_ms / 1000
AUDIO_CHUNK_BYTES = int(
    AUDIO_SAMPLE_RATE * AUDIO_SAMPLE_WIDTH * AUDIO_CHANNELS * AUDIO_CHUNK_MS / 1000
)  # = 640 bytes

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "core": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
