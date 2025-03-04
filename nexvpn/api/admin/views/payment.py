import logging
import tempfile
import uuid
from datetime import timedelta

import yookassa
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view
from rest_framework.response import Response
from yookassa.domain.response import PaymentResponse

from nexvpn.api.admin.serializers.payment_serializers import PaymentRequestSerializers, PaymentResponseSerializer
from nexvpn.utils import gen_yookassa_payment_data
from nexvpn.enums import TransactionTypeEnum, TransactionStatusEnum
from nexvpn.models import Transaction, NexUser, Payment


@extend_schema(request=PaymentRequestSerializers, responses={201: PaymentResponseSerializer}, tags=["payments"])
@api_view(["POST"])
def create_payment(request, user_id: int) -> Response:
    user = get_object_or_404(NexUser, pk=user_id)
    serializer = PaymentRequestSerializers(data=request.data)
    serializer.is_valid(raise_exception=True)

    value = serializer.validated_data["value"]

    old_transaction = Transaction.objects.filter(
        user=user,
        value=value,
        status=TransactionStatusEnum.WAITING_FOR_CAPTURE,
        created_at__gt=now() - timedelta(minutes=10),
        payment__isnull=False
    ).first()
    existing_payment = old_transaction.payment if old_transaction else None
    payment_data = gen_yookassa_payment_data(value)
    try:
        with transaction.atomic():

            if existing_payment:
                idempotence_key = existing_payment.idempotence_key
                yookassa_payment: PaymentResponse = yookassa.Payment.create(payment_data, idempotence_key)
            else:
                idempotence_key = uuid.uuid4()
                yookassa_payment: PaymentResponse = yookassa.Payment.create(payment_data, idempotence_key)
                payment = Payment.objects.create(uuid=yookassa_payment.id, idempotence_key=idempotence_key)
                Transaction.objects.create(
                    user=user,
                    is_credit=True,
                    value=value,
                    payment=payment,
                    type=TransactionTypeEnum.FILL_UP_BALANCE,
                    status=TransactionStatusEnum.WAITING_FOR_CAPTURE
                )
            if yookassa_payment.status != 'pending':
                raise Exception(f"Payment status is not pending")
            url = yookassa_payment.confirmation.confirmation_url
            response_serializer = PaymentResponseSerializer(data={"url": url})
            response_serializer.is_valid(raise_exception=True)
            return Response(response_serializer.validated_data, status=201)
    except TypeError or AttributeError as e:
        error_msg = f"Cannot create yookassa payment:\npayment_data:{payment_data}\n{e}"
    except Exception as e:
        error_msg = f"Cannot create yookassa payment:\n{e}\npayment_json: {yookassa_payment.json()}"

    logging.error(error_msg, exc_info=True)  # noqa
    return Response({"detail": error_msg}, status=400)


@extend_schema(tags=["payments"])
@api_view(["GET"])
def get_transactions_history(request, user_id: int) -> FileResponse:
    user = get_object_or_404(NexUser, pk=user_id)
    res = f"Данные актуальны на момент {now().strftime("%d.%m.%Y %H:%M:%S")}.\n\n"
    res += f"Текущий баланс: {user.balance}₽\n\n"
    res += "История транзакций:\n"
    transactions = Transaction.objects.filter(user_id=user_id).order_by("-created_at")
    res += "\n".join(map(str, transactions))

    with tempfile.NamedTemporaryFile(mode="w+", delete=True) as temp_file:
        temp_file.write(res)
        temp_file.flush()
        response = FileResponse(open(temp_file.name, "rb"), as_attachment=True, filename=f"transactions.txt")
        return response
