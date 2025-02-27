from rest_framework.generics import ListAPIView

from nexvpn.api.admin.serializers.server_serializers import ServerSerializer
from nexvpn.models import Server


class ListServersView(ListAPIView):
    serializer_class = ServerSerializer
    queryset = Server.objects.filter(is_active=True)
