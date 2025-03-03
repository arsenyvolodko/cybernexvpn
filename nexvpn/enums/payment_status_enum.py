from django.db.models import TextChoices


class PaymentStatusEnum(TextChoices):
    SUCCEEDED = "payment.succeeded"
    CANCELED = "payment.canceled"
