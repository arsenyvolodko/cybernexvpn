import logging

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from nexvpn import permissions
from nexvpn.api.admin.serializers.payment_serializers import PlanChangeQuoteSerializer
from nexvpn.api.admin.serializers.subscription_serializers import (
    PlanChangeRequestSerializer,
    PlanSerializer,
    SubscriptionSerializer,
)
from nexvpn.models import NexUser, Plan, Subscription
from nexvpn.permissions import check_ownership
from nexvpn.remnawave import RemnawaveError
from nexvpn.subscription import panel_sync, service

logger = logging.getLogger(__name__)


@extend_schema(responses={200: PlanSerializer(many=True)}, tags=["subscription"])
@api_view(["GET"])
@permission_classes([permissions.IsAdminOrUser])
def list_plans(request) -> Response:
    plans = Plan.objects.filter(is_active=True, is_public=True)
    return Response(PlanSerializer(plans, many=True).data)


def _get_subscription(user_id: int) -> Subscription:
    subscription = get_object_or_404(
        Subscription.objects.select_related("plan", "next_plan", "user"), user_id=user_id
    )
    # Отложенное понижение применяется лениво, при первом же обращении после
    # окончания периода — планировщик для этого не нужен.
    return service.ensure_current_plan(subscription)


@extend_schema(responses={200: SubscriptionSerializer}, tags=["subscription"])
@api_view(["GET"])
@permission_classes([permissions.IsAdminOrUser])
def get_subscription(request, user_id: int) -> Response:
    check_ownership(request, user_id)
    return Response(SubscriptionSerializer(_get_subscription(user_id)).data)


@extend_schema(
    request=PlanChangeRequestSerializer, responses={200: PlanChangeQuoteSerializer}, tags=["subscription"]
)
@api_view(["POST"])
@permission_classes([permissions.IsAdminOrUser])
def quote_plan_change(request, user_id: int) -> Response:
    """Что будет при переходе на другой тариф: сколько дней и сколько доплатить."""
    check_ownership(request, user_id)
    user = get_object_or_404(NexUser, pk=user_id)

    serializer = PlanChangeRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    new_plan = get_object_or_404(Plan, device_limit=serializer.validated_data["device_limit"], is_active=True)

    try:
        quote = service.quote_plan_change(user, new_plan)
    except service.SubscriptionError as exc:
        return Response({"error_message": str(exc)}, status=400)

    return Response(PlanChangeQuoteSerializer(quote).data)


@extend_schema(
    request=PlanChangeRequestSerializer, responses={200: SubscriptionSerializer}, tags=["subscription"]
)
@api_view(["POST"])
@permission_classes([permissions.IsAdminOrUser])
def change_plan(request, user_id: int) -> Response:
    """Бесплатный переход: остаток пересчитывается по цене нового тарифа.

    Понижение оформляется как отложенное — до конца оплаченного периода
    пользователь сохраняет все устройства, за которые заплатил.
    """
    check_ownership(request, user_id)
    user = get_object_or_404(NexUser, pk=user_id)

    serializer = PlanChangeRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    new_plan = get_object_or_404(Plan, device_limit=serializer.validated_data["device_limit"], is_active=True)

    subscription = _get_subscription(user_id)
    try:
        if new_plan.price_month < subscription.plan.price_month:
            subscription = service.schedule_plan_downgrade(user, new_plan)
        else:
            subscription = service.change_plan_now(user, new_plan)
    except service.SubscriptionError as exc:
        return Response({"error_message": str(exc)}, status=400)

    panel_sync.sync_subscription(subscription)
    return Response(SubscriptionSerializer(subscription).data)


@extend_schema(tags=["subscription"])
@api_view(["GET"])
@permission_classes([permissions.IsAdminOrUser])
def list_devices(request, user_id: int) -> Response:
    """Устройства из Remnawave. Дублировать их в Django нельзя — рассинхрон."""
    check_ownership(request, user_id)
    subscription = _get_subscription(user_id)
    try:
        devices = panel_sync.list_devices(subscription)
    except RemnawaveError as exc:
        logger.warning("Не удалось получить устройства пользователя %s: %s", user_id, exc)
        return Response({"error_message": "Панель временно недоступна."}, status=503)
    return Response({"limit": subscription.device_limit, "devices": devices})


@extend_schema(tags=["subscription"])
@api_view(["DELETE"])
@permission_classes([permissions.IsAdminOrUser])
def delete_device(request, user_id: int, hwid: str) -> Response:
    check_ownership(request, user_id)
    subscription = _get_subscription(user_id)
    try:
        panel_sync.remove_device(subscription, hwid)
    except RemnawaveError as exc:
        logger.warning("Не удалось удалить устройство %s у %s: %s", hwid, user_id, exc)
        return Response({"error_message": "Панель временно недоступна."}, status=503)
    return Response(status=204)
