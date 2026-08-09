import json
import logging

from django.db import transaction
from django.utils.timezone import now
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response
from yookassa.domain.notification import WebhookNotification

from nexvpn.enums import PaymentKindEnum, PaymentStatusEnum, TransactionStatusEnum
from nexvpn.models import Payment, Transaction
from nexvpn.subscription import panel_sync, service

logger = logging.getLogger(__name__)

EXPECTED_WEBHOOK_TYPE = "notification"


@api_view(["POST"])
def handle_notification(request: Request) -> Response:
    """Уведомление YooKassa.

    Почти всегда отвечаем 200, даже на мусор: иначе YooKassa будет слать
    повторы сутки. Реальная защита от двойного начисления — `processed_at`.
    """
    try:
        webhook = WebhookNotification(json.loads(request.body))
    except Exception as exc:
        logger.error("Не удалось разобрать вебхук: %s", exc)
        return Response(status=400)

    if not (webhook.type == EXPECTED_WEBHOOK_TYPE and webhook.event in PaymentStatusEnum.values):
        return Response(status=200)

    new_status = PaymentStatusEnum(value=webhook.event)
    payment_id = webhook.object.id

    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(uuid=payment_id).first()
        if payment is None:
            logger.warning("Вебхук по неизвестному платежу: %s", payment_id)
            return Response(status=200)

        payment_transaction = Transaction.objects.filter(payment=payment).first()

        if new_status == PaymentStatusEnum.CANCELED:
            if payment_transaction:
                payment_transaction.status = TransactionStatusEnum.FAILED
                payment_transaction.save(update_fields=["status"])
            return Response(status=200)

        if payment.processed_at is not None:
            logger.info("Повторный вебхук по уже обработанному платежу %s", payment_id)
            return Response(status=200)

        if payment_transaction:
            payment_transaction.status = TransactionStatusEnum.SUCCEEDED
            payment_transaction.save(update_fields=["status"])

        subscription = _apply_payment(payment)
        payment.processed_at = now()
        payment.save(update_fields=["processed_at"])

    if subscription is not None:
        # Панель дёргаем уже после коммита: её недоступность не должна откатывать оплату.
        panel_sync.sync_subscription(subscription)

    return Response(status=200)


def _apply_payment(payment: Payment):
    """Выдать то, за что заплачено. Условия берём из Payment, не из вебхука."""
    if payment.user is None or payment.plan is None:
        logger.error("Платёж %s без пользователя или тарифа — начислять нечего", payment.uuid)
        return None

    if payment.kind == PaymentKindEnum.PLAN_CHANGE:
        return service.change_plan_now(
            user=payment.user,
            new_plan=payment.plan,
            amount_paid=payment.amount or 0,
            payment=payment,
        )

    return service.purchase_period(
        user=payment.user,
        plan=payment.plan,
        amount=payment.amount or 0,
        payment=payment,
    )
