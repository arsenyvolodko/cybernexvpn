from django.urls import path

from nexvpn.api.telemetry.views import ingest_inbound_usage

urlpatterns = [
    path("inbound-usage/", ingest_inbound_usage),
]
