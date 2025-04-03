from django.conf import settings
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import permission_classes
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from nexvpn import permissions
from nexvpn.api.admin.serializers.user_serializers import NexUserSerializer
from nexvpn.enums import TransactionTypeEnum
from nexvpn.models import NexUser, UserBalance, Transaction


@extend_schema(tags=["users"])
@permission_classes([permissions.IsAdmin])
class UsersViewSet(ModelViewSet):
    queryset = NexUser.objects.all()
    serializer_class = NexUserSerializer
    lookup_field = "id"
    lookup_url_kwarg = "user_id"

    def perform_create(self, serializer):
        user_id = self.kwargs.get("user_id")
        with transaction.atomic():
            user = serializer.save(id=user_id)
            UserBalance.objects.create(user=user, value=settings.START_BALANCE)
            Transaction.objects.create(
                user=user,
                is_credit=True,
                value=settings.START_BALANCE,
                type=TransactionTypeEnum.START_BALANCE,
            )

    def create(self, request, *args, **kwargs):
        user_id = self.kwargs.get("user_id")

        if self.get_queryset().filter(id=user_id).exists():
            return Response(
                {"detail": "User with that ID already exists"},
                status=400,
            )

        return super().create(request, *args, **kwargs)
