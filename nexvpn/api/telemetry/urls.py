from django.urls import path

from nexvpn.api.telemetry.probe import ingest_sub_probe
from nexvpn.api.telemetry.views import (
    ingest_inbound_usage,
    ingest_link_usage,
    ingest_relay_networks,
)

urlpatterns = [
    path("inbound-usage/", ingest_inbound_usage),
    path("relay-networks/", ingest_relay_networks),
    path("link-usage/", ingest_link_usage),
    path("sub-probe/", ingest_sub_probe),
]
