from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view
from rest_framework.response import Response

from nexvpn.api.admin.serializers.promo_code_serializers import PromoCodeRequestSerializer
from nexvpn.enums import TransactionTypeEnum
from nexvpn.models import NexUser, PromoCode, UsedPromoCode, UserBalance, Transaction


@extend_schema(tags=["users"], request=PromoCodeRequestSerializer)
@api_view(["POST"])
def apply_promo_code(request, user_id: int):
    user = get_object_or_404(NexUser, pk=user_id)

    serializer = PromoCodeRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    name = serializer.validated_data["code"]
    code = PromoCode.objects.filter(name=name).first()

    invalid_code_msg = "Похоже, такого промокода не существует."
    used_code_msg = "Вы уже использовали данный промокод."

    if not code:
        return Response({"error_message": invalid_code_msg}, status=400)

    if UsedPromoCode.objects.filter(user=user, promo_code=code).exists():
        return Response({"error_message": used_code_msg}, status=400)

    if not code.is_active:
        return Response({"error_message": invalid_code_msg}, status=400)

    if not code.public_access and not code.allowed_users.filter(user=user).exists():
        return Response({"error_message": invalid_code_msg}, status=400)

    with transaction.atomic():
        UsedPromoCode.objects.create(user=user, promo_code=code)

        user_balance = UserBalance.objects.get(user=user)
        user_balance.value += code.value
        user_balance.save()

        Transaction.objects.create(
            user=user,
            is_credit=True,
            value=code.value,
            promo_code=code,
            type=TransactionTypeEnum.PROMO_CODE,
        )

    return Response()
