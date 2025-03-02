from django.db import models


class TransactionStatusEnum(models.TextChoices):
    WAITING_FOR_CAPTURE = "waiting_for_capture", "Ожидается оплата"
    SUCCEEDED = "succeeded", "Успешный платеж"
    FAILED = "failed", "Неуспешный платеж"
