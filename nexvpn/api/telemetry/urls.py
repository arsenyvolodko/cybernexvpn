from django.urls import path

from nexvpn.api.telemetry.views import ingest_inbound_usage, ingest_relay_networks

urlpatterns = [
    path("inbound-usage/", ingest_inbound_usage),
    path("relay-networks/", ingest_relay_networks),
]
