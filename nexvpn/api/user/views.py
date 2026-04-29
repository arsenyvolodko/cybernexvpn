from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from nexvpn.permissions import IsUser
from nexvpn.api.admin.serializers.user_serializers import NexUserSerializer, NexUserUpdateSerializer


@extend_schema(
    tags=["users"],
    methods=["GET"],
    responses={200: NexUserSerializer},
)
@extend_schema(
    tags=["users"],
    methods=["PATCH"],
    request=NexUserUpdateSerializer,
    responses={200: NexUserSerializer},
)
@api_view(["GET", "PATCH"])
@permission_classes([IsUser])
def me(request: Request) -> Response:
    if request.method == "PATCH":
        serializer = NexUserUpdateSerializer(request.nexuser, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
    return Response(NexUserSerializer(request.nexuser).data)
