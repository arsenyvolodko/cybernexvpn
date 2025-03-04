from drf_spectacular.utils import extend_schema
from rest_framework.generics import ListAPIView

from nexvpn.api.admin.serializers.server_serializers import ServerSerializer
from nexvpn.models import Server


@extend_schema(tags=["servers"])
class ListServersView(ListAPIView):
    serializer_class = ServerSerializer
    queryset = Server.objects.filter(is_active=True)
