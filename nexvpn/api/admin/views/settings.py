from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from nexvpn import permissions
from nexvpn.api.admin.serializers.settings_serializers import MaintenanceSerializer
from nexvpn.models import GlobalSettings


@extend_schema(responses={200: MaintenanceSerializer}, tags=["settings"])
@api_view(["GET"])
@permission_classes([permissions.IsAdminOrUser])
def get_maintenance(request) -> Response:
    """Состояние режима техработ — бот спрашивает его перед обработкой апдейта.

    Флаг живёт в БД, поэтому заглушку можно включить и снять из админки, не
    перезапуская бота.
    """
    settings_obj = GlobalSettings.load()
    return Response(MaintenanceSerializer(settings_obj).data)
