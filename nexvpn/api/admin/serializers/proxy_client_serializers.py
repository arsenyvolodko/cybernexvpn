from rest_framework import serializers

from nexvpn.models import ProxyClient
from nexvpn.proxy_utils import get_proxy_server_config, get_proxy_url


class ProxyClientSerializer(serializers.ModelSerializer):
    proxy_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProxyClient
        fields = [
            "id",
            "user",
            "is_active",
            "auto_renew",
            "created_at",
            "end_date",
            "secret",
            "proxy_url",
        ]

    def get_proxy_url(self, obj: ProxyClient | None) -> str | None:
        if obj is None:
            return None
        try:
            proxy_server = get_proxy_server_config()
            return get_proxy_url(proxy_server, obj)
        except Exception:
            return None
