import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from nexvpn import permissions
from nexvpn.api.admin.serializers.user_serializers import NexUserSerializer
from nexvpn.models import NexUser
from nexvpn.subscription import panel_sync, service

logger = logging.getLogger(__name__)


@extend_schema(tags=["users"])
@permission_classes([permissions.IsAdmin])
class UsersViewSet(ModelViewSet):
    queryset = NexUser.objects.all()
    serializer_class = NexUserSerializer
    lookup_field = "id"
    lookup_url_kwarg = "user_id"

    def perform_create(self, serializer):
        """Новый пользователь: `id` == telegram id, сразу пробный период."""
        user_id = self.kwargs.get("user_id")
        with transaction.atomic():
            user = serializer.save(id=user_id, activated_at=now())
            subscription = service.grant_trial(user)
        if subscription is not None:
            panel_sync.sync_subscription(subscription)

    def create(self, request, *args, **kwargs):
        user_id = self.kwargs.get("user_id")
        if self.get_queryset().filter(id=user_id).exists():
            return Response({"detail": "User with that ID already exists"}, status=400)
        return super().create(request, *args, **kwargs)


@extend_schema(tags=["users"], responses={200: NexUserSerializer})
@api_view(["POST"])
@permission_classes([permissions.IsAdmin])
def activate_user(request, user_id: int) -> Response:
    """Отметить, что пользователь зашёл в бота новой версии.

    Для перенесённых из старой базы это единственное, что происходит: пробный
    период им не полагается, дни у них уже начислены миграцией.
    """
    user = get_object_or_404(NexUser, pk=user_id)
    if user.activated_at is None:
        user.activated_at = now()
        user.save(update_fields=["activated_at"])
    return Response(NexUserSerializer(user).data)
