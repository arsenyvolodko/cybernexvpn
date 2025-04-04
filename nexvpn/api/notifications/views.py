import asyncio
import json
import logging

from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response
from yookassa.domain.notification import WebhookNotification

from nexvpn.utils import succeed_payment
from nexvpn.enums import PaymentStatusEnum, TransactionStatusEnum
from nexvpn.models import Payment, Transaction, UserBalance

EXPECTED_WEBHOOK_TYPE = "notification"


@api_view(["POST"])
def handle_notification(request: Request) -> Response:
    payment_json = json.loads(request.body)
    try:
        webhook = WebhookNotification(payment_json)
    except Exception as e:
        logging.error(f"Error parsing webhook: {e}")
        return Response(status=400)

    if not (webhook.type == EXPECTED_WEBHOOK_TYPE and webhook.event in PaymentStatusEnum.values):
        # Return 200 to YooKassa to avoid repeated requests
        return Response(status=200)

    new_status = PaymentStatusEnum(value=webhook.event)
    payment_obj = webhook.object

    payment = Payment.objects.filter(id=payment_obj.id).first()
    payment_transaction: Transaction = Transaction.objects.filter(payment__isnull=False, payment=payment).first()

    if not (payment and payment_transaction):
        logging.info(f"Cannot process webhook: payment or transaction not found:\n"
                     f"payment_id={payment_obj.id}, transaction_id={payment_transaction.id}.\n"
                     f"Webhook data: {payment_json}")

        # Return 200 to YooKassa to avoid repeated requests
        return Response(status=200)

    if new_status == PaymentStatusEnum.CANCELED:
        payment_transaction.status = TransactionStatusEnum.FAILED
        payment_transaction.save()
        return Response(status=200)

    # Payment succeeded
    with transaction.atomic():
        payment_transaction.status = TransactionStatusEnum.SUCCEEDED
        payment_transaction.save()

        user_balance = UserBalance.objects.select_for_update().get(user=payment_transaction.user)
        user_balance.value += payment_transaction.value
        user_balance.save()

        asyncio.run(succeed_payment(payment_id=str(payment.uuid), user_id=payment_transaction.user.id))
        return Response(status=200)
