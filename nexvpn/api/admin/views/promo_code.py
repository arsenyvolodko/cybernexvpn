from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from nexvpn import permissions
from nexvpn.api.admin.serializers.promo_code_serializers import (
    PromoCodeRequestSerializer,
    PromoCodeResponseSerializer,
)
from nexvpn.enums import SubscriptionEventReasonEnum
from nexvpn.models import NexUser, PromoCode, UsedPromoCode
from nexvpn.subscription import panel_sync, service


@extend_schema(tags=["users"], request=PromoCodeRequestSerializer, responses=PromoCodeResponseSerializer)
@api_view(["POST"])
@permission_classes([permissions.IsAdmin])
def apply_promo_code(request, user_id: int):
    """Промокод даёт дни подписки, а не рубли — баланса больше нет."""
    user = get_object_or_404(NexUser, pk=user_id)

    serializer = PromoCodeRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    code = PromoCode.objects.filter(name=serializer.validated_data["code"]).first()

    invalid_code_msg = "Похоже, такого промокода не существует."

    if not code or not code.is_active:
        return Response({"error_message": invalid_code_msg}, status=400)
    if UsedPromoCode.objects.filter(user=user, promo_code=code).exists():
        return Response({"error_message": "Вы уже использовали данный промокод."}, status=400)
    if not code.public_access and not code.allowed_users.filter(user=user).exists():
        return Response({"error_message": invalid_code_msg}, status=400)
    if not code.bonus_days:
        return Response({"error_message": invalid_code_msg}, status=400)

    with transaction.atomic():
        UsedPromoCode.objects.create(user=user, promo_code=code)
        try:
            subscription = service.grant_days(
                user,
                days=code.bonus_days,
                reason=SubscriptionEventReasonEnum.PROMO_CODE,
                plan=None if hasattr(user, "subscription") else service.trial_plan(),
                comment=f"Промокод {code.name}",
            )
        except service.SubscriptionError as exc:
            return Response({"error_message": str(exc)}, status=400)

    panel_sync.sync_subscription(subscription)

    response_serializer = PromoCodeResponseSerializer(data={"bonus_days": code.bonus_days})
    response_serializer.is_valid(raise_exception=True)
    return Response(response_serializer.validated_data)
