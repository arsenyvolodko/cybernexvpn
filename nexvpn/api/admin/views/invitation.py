from django.conf import settings
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from nexvpn import permissions
from nexvpn.api.admin.serializers.invitation_serializers import InvitationRequestSerializer
from nexvpn.models import NexUser
from nexvpn.subscription import service


@extend_schema(tags=["users"], request=InvitationRequestSerializer)
@api_view(["POST"])
@permission_classes([permissions.IsAdmin])
def apply_invitation(request, *args, **kwargs):
    """Переход по реферальной ссылке.

    Дней здесь никто не получает: бонусы начисляются только после первой
    оплаты приглашённого. Инвайтеру бот сразу пишет, что бонус его ждёт —
    отсюда возвращается всё, что для этого сообщения нужно.
    """
    invitee_id = kwargs.get("user_id")
    serializer = InvitationRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    inviter = get_object_or_404(NexUser, pk=serializer.validated_data["inviter"])
    invitee = get_object_or_404(NexUser, pk=invitee_id)

    try:
        invitation = service.register_invitation(inviter, invitee)
    except service.SubscriptionError as exc:
        return Response({"error_message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {
            "inviter": inviter.pk,
            "invitee": invitee.pk,
            "inviter_bonus_days": settings.REFERRAL_INVITER_DAYS,
            "invitee_bonus_days": settings.REFERRAL_INVITEE_DAYS,
            "granted": invitation.reward_granted_at is not None,
        }
    )
