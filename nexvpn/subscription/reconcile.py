"""Добор платежей опросом ЮKassa.

Штатно оплату подтверждает вебхук. Но полагаться только на него нельзя: наш
хостер оказался у ЮKassa в блоке, и уведомления до сервера не доходят вовсе —
человек платит, деньги списываются, а дней не прибавляется. Хуже ситуации в
платном сервисе не бывает.

Поэтому мы сами спрашиваем ЮKassa о судьбе каждого незакрытого платежа. Это
не замена вебхуку, а страховка: что придёт раньше, то и начислит, повторное
начисление отсекает `processed_at`.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils.timezone import now

from nexvpn.models import Payment

logger = logging.getLogger(__name__)

API = "https://api.yookassa.ru/v3/payments"
# Дольше суток ЮKassa платёж не держит: неоплаченный сам отменяется.
MAX_AGE = timedelta(days=2)


@dataclass
class ReconcileResult:
    checked: int = 0
    applied: int = 0
    still_pending: int = 0
    failed: int = 0


def pending_payments():
    """Платежи, за которые мы ещё ничего не выдали."""
    return (
        Payment.objects.filter(processed_at=None, created_at__gte=now() - MAX_AGE)
        .exclude(user=None)
        .select_related("user", "plan")
        .order_by("created_at")
    )


def _status_of(payment: Payment) -> str | None:
    try:
        response = requests.get(
            f"{API}/{payment.uuid}",
            headers={"Authorization": f"Bearer {settings.YOOKASSA_OAUTH_TOKEN}"},
            timeout=settings.YOOKASSA_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("Не смогли спросить ЮKassa о платеже %s: %s", payment.uuid, exc)
        return None
    if not response.ok:
        logger.warning("ЮKassa вернула %s по платежу %s", response.status_code, payment.uuid)
        return None
    return response.json().get("status")


def notify_paid(payment: Payment, subscription) -> None:
    """Сообщить человеку, что оплата прошла.

    Живёт здесь, чтобы вебхук и сверка звали одно и то же: два похожих, но
    разных сообщения об одном событии — верный способ однажды отправить оба.
    """
    from bot import texts
    from bot.notify import notify_payment_applied

    if subscription is None or payment.user_id is None:
        return
    from django.utils.timezone import localtime

    notify_payment_applied(
        payment.user_id,
        texts.PAYMENT_APPLIED.format(
            plan=texts.plural_devices(subscription.plan.device_limit),
            until=localtime(subscription.expires_at).strftime("%d.%m.%Y"),
            days_left=texts.plural_days(subscription.days_left),
        ),
    )


def reconcile() -> ReconcileResult:
    from nexvpn.api.notifications.views import _apply_payment
    from nexvpn.subscription import panel_sync

    result = ReconcileResult()
    for payment in pending_payments():
        result.checked += 1
        status = _status_of(payment)
        if status is None:
            result.failed += 1
            continue
        if status != "succeeded":
            result.still_pending += 1
            continue

        with transaction.atomic():
            # Перечитываем под блокировкой: вебхук мог опередить нас на
            # миллисекунды, и начислять второй раз нельзя.
            locked = Payment.objects.select_for_update().get(pk=payment.pk)
            if locked.processed_at is not None:
                continue
            subscription = _apply_payment(locked)
            locked.processed_at = now()
            locked.save(update_fields=["processed_at"])

        result.applied += 1
        logger.info("Добрали платёж %s на %s₽ для %s", locked.uuid, locked.amount, locked.user_id)
        if subscription is not None:
            panel_sync.sync_subscription(subscription)
        notify_paid(locked, subscription)

    if result.applied or result.failed:
        logger.info(
            "Сверка платежей: проверено %s, начислено %s, ещё ждут %s, не спросили %s",
            result.checked, result.applied, result.still_pending, result.failed,
        )
    return result
