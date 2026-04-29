import logging
import tempfile

logger = logging.getLogger(__name__)
import uuid
from datetime import timedelta

import yookassa
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from yookassa.domain.response import PaymentResponse

from nexvpn import permissions
from nexvpn.permissions import check_ownership
from nexvpn.api.admin.serializers.payment_serializers import PaymentRequestSerializers, PaymentResponseSerializer
from nexvpn.utils import gen_yookassa_payment_data
from nexvpn.enums import TransactionTypeEnum, TransactionStatusEnum
from nexvpn.models import Transaction, NexUser, Payment


@extend_schema(request=PaymentRequestSerializers, responses={201: PaymentResponseSerializer}, tags=["payments"])
@api_view(["POST"])
@permission_classes([permissions.IsAdminOrUser])
def create_payment(request, user_id: int) -> Response:
    check_ownership(request, user_id)
    user = get_object_or_404(NexUser, pk=user_id)
    serializer = PaymentRequestSerializers(data=request.data)
    serializer.is_valid(raise_exception=True)

    value = serializer.validated_data["value"]
    return_url = serializer.validated_data["return_url"]
    email = serializer.validated_data["email"]
    if email and user.email != email:
        user.email = email
        user.save(update_fields=["email"])
    payment_data = gen_yookassa_payment_data(value, return_url, email)
    try:
        with transaction.atomic():
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
    except Exception as e:
        logger.error(f"Cannot create yookassa payment: {e}", exc_info=True)
        return Response({"detail": "Payment creation failed"}, status=400)


@extend_schema(tags=["payments"])
@api_view(["GET"])
@permission_classes([permissions.IsAdminOrUser])
def get_transactions_history(request, user_id: int) -> FileResponse:
    check_ownership(request, user_id)
    user = get_object_or_404(NexUser, pk=user_id)
    res = f"Данные актуальны на момент {now().strftime("%d.%m.%Y %H:%M:%S")}.\n\n"
    res += f"Текущий баланс: {user.balance}₽\n\n"
    res += "История транзакций:\n\n"
    transactions = Transaction.objects.filter(user_id=user_id).order_by("-created_at")
    res += "\n\n".join(map(str, transactions))

    with tempfile.NamedTemporaryFile(mode="w+", delete=True) as temp_file:
        temp_file.write(res)
        temp_file.flush()
        response = FileResponse(open(temp_file.name, "rb"), as_attachment=True, filename=f"transactions.txt")
        return response
