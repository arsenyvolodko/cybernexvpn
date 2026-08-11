from django.db import models


class PaymentKindEnum(models.TextChoices):
    PURCHASE = "purchase", "Оплата периода подписки"
    PLAN_CHANGE = "plan_change", "Доплата за смену тарифа"
    # Проверочный: реальные деньги, но ничего не начисляет. Нужен, чтобы
    # убедиться, что подтверждение от ЮKassa до нас доходит.
    TEST = "test", "Проверочный платёж"
