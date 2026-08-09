from django.db import models


class PaymentKindEnum(models.TextChoices):
    PURCHASE = "purchase", "Оплата периода подписки"
    PLAN_CHANGE = "plan_change", "Доплата за смену тарифа"
