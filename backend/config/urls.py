"""
URL configuration for the Voice Activator REST API.
"""

from django.urls import path

from core import views

urlpatterns = [
    path("api/health/", views.health_check, name="health-check"),
    path("api/status/", views.system_status, name="system-status"),
]
