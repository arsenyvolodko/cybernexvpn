import logging
import tempfile

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from nexvpn import permissions, payments
from nexvpn.api.admin.serializers.payment_serializers import (
    PaymentRequestSerializer,
    PaymentResponseSerializer,
)
from nexvpn.api.exceptions.enums.error_message_enum import ErrorMessageEnum
from nexvpn.enums import PaymentKindEnum
from nexvpn.models import GlobalSettings, NexUser, Plan, SubscriptionEvent, Transaction
from nexvpn.permissions import check_ownership
from nexvpn.subscription import pricing, service

logger = logging.getLogger(__name__)


def _resolve_amount(user: NexUser, kind: str, plan: Plan) -> tuple[int, int]:
    """Сколько платить и сколько дней за это дадут. Считается только на сервере."""
    if kind == PaymentKindEnum.PLAN_CHANGE:
        quote = service.quote_plan_change(user, plan)
        if quote.topup_price is None:
            raise service.SubscriptionError(
                "При таком остатке переход бесплатный — доплата не нужна"
            )
        return quote.topup_price, quote.topup_days
    return plan.price_month, pricing.days_in_period()


@extend_schema(request=PaymentRequestSerializer, responses={201: PaymentResponseSerializer}, tags=["payments"])
@api_view(["POST"])
@permission_classes([permissions.IsAdminOrUser])
def create_payment(request, user_id: int) -> Response:
    check_ownership(request, user_id)
    user = get_object_or_404(NexUser, pk=user_id)

    serializer = PaymentRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    kind = serializer.validated_data["kind"]
    email = serializer.validated_data["email"]
    return_url = serializer.validated_data["return_url"]

    plan = Plan.objects.filter(device_limit=serializer.validated_data["device_limit"], is_active=True).first()
    if plan is None:
        return Response({"error_message": "Такого тарифа нет."}, status=400)

    if email and user.email != email:
        user.email = email
        user.save(update_fields=["email"])

    # Email обязателен, только пока выписываются чеки: без него их некуда слать.
    billing = GlobalSettings.load()
    if billing.receipt_enabled and not user.email:
        return Response({"error_message": ErrorMessageEnum.NO_EMAIL_ERROR_MESSAGE.value}, status=400)

    try:
        amount, days = _resolve_amount(user, kind, plan)
    except service.SubscriptionError as exc:
        return Response({"error_message": str(exc)}, status=400)

    if amount <= 0:
        return Response({"error_message": "Оплата не требуется."}, status=400)

    try:
        created = payments.create_payment(
            user, plan, amount=amount, days=days, kind=kind, return_url=return_url,
        )
    except Exception as exc:
        logger.error("Не удалось создать платёж в YooKassa: %s", exc, exc_info=True)
        return Response({"error_message": "Не получилось создать платёж, попробуйте позже."}, status=400)

    response_serializer = PaymentResponseSerializer(
        data={
            "url": created.url,
            "amount": created.amount,
            "payment_id": str(created.payment.uuid),
        }
    )
    response_serializer.is_valid(raise_exception=True)
    return Response(response_serializer.validated_data, status=201)


@extend_schema(tags=["payments"])
@api_view(["GET"])
@permission_classes([permissions.IsAdminOrUser])
def get_transactions_history(request, user_id: int) -> FileResponse:
    check_ownership(request, user_id)
    user = get_object_or_404(NexUser, pk=user_id)

    lines = [f"Данные актуальны на момент {now().strftime('%d.%m.%Y %H:%M:%S')}.", ""]

    subscription = getattr(user, "subscription", None)
    if subscription:
        lines.append(
            f"Текущая подписка: {subscription.plan.device_limit} устр. "
            f"до {subscription.expires_at.strftime('%d.%m.%Y')} "
            f"({subscription.days_left} дн. осталось)."
        )
    else:
        lines.append("Подписки нет.")

    lines += ["", "История подписки:", ""]
    lines += [str(event) for event in SubscriptionEvent.objects.filter(user=user)]
    lines += ["", "История платежей:", ""]
    lines += [str(item) for item in Transaction.objects.filter(user=user).order_by("-created_at")]

    with tempfile.NamedTemporaryFile(mode="w+", delete=True, encoding="utf-8") as temp_file:
        temp_file.write("\n".join(lines))
        temp_file.flush()
        return FileResponse(open(temp_file.name, "rb"), as_attachment=True, filename="history.txt")
