from drf_spectacular.utils import extend_schema
from rest_framework.generics import ListAPIView, RetrieveAPIView

from nexvpn.api.admin.serializers.server_serializers import ServerSerializer
from nexvpn.models import Server


@extend_schema(tags=["servers"])
class ListServersView(ListAPIView):
    serializer_class = ServerSerializer
    queryset = Server.objects.filter(is_active=True)


@extend_schema(tags=["servers"])
class RetrieveServerView(RetrieveAPIView):
    queryset = Server.objects.filter(is_active=True)
    serializer_class = ServerSerializer
    lookup_field = "id"
    lookup_url_kwarg = "server_id"
