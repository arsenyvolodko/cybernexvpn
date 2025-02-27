from django.db import models


class PaymentStatusEnum(models.TextChoices):
    PENDING = "pending", "В обработке"
    SUCCEEDED = "succeeded", "Успешный платеж"
    CANCELED = "canceled", "Отмененный платеж"
