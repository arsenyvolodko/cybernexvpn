from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from nexvpn.api.admin.serializers.invitation_serializer import InvitationRequestSerializer
from nexvpn.enums import TransactionTypeEnum
from nexvpn.models import NexUser, UserInvitation, UserBalance, Transaction


@extend_schema(tags=["users"], request=InvitationRequestSerializer)
@api_view(["POST"])
def apply_invitation(request, *args, **kwargs):

    invitee_id = kwargs.get("user_id")
    serializer = InvitationRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    inviter_id = serializer.validated_data["inviter"]

    if inviter_id == invitee_id:
        return Response({"error_message": "Приглашенный пользователь и пригласитель должны отличаться."},
                        status=status.HTTP_400_BAD_REQUEST)

    inviter = get_object_or_404(NexUser, pk=inviter_id)
    invitee = get_object_or_404(NexUser, pk=invitee_id)

    if UserInvitation.objects.filter(invitee=invitee).exists():
        return Response({"error_message": "Пользователь уже был приглашен."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        UserInvitation.objects.create(
            inviter=inviter,
            invitee=invitee
        )

        Transaction.objects.create(
            user=inviter,
            is_credit=True,
            value=settings.INVITATION_BONUS,
            type=TransactionTypeEnum.INVITATION,
        )

        user_balance = UserBalance.objects.select_for_update().get(user=inviter)
        user_balance.value += settings.INVITATION_BONUS
        user_balance.save()

        return Response()
