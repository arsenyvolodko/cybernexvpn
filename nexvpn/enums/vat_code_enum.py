from django.db import models


class VatCodeEnum(models.IntegerChoices):
    """Коды ставки НДС в чеке YooKassa.

    Ставки намеренно названы без процентов: конкретные значения меняются
    законом, а код в чеке — нет. Самозанятому (НПД) нужен `NO_VAT`.
    """

    NO_VAT = 1, "1 — без НДС"
    ZERO = 2, "2 — НДС по ставке 0%"
    REDUCED = 3, "3 — НДС по пониженной ставке"
    STANDARD = 4, "4 — НДС по основной ставке"
    REDUCED_CALCULATED = 5, "5 — НДС по расчётной пониженной ставке"
    STANDARD_CALCULATED = 6, "6 — НДС по расчётной основной ставке"


class PaymentSubjectEnum(models.TextChoices):
    SERVICE = "service", "Услуга"
    COMMODITY = "commodity", "Товар"
    WORK = "work", "Работа"
    PAYMENT = "payment", "Платёж"
    ANOTHER = "another", "Иное"


class PaymentModeEnum(models.TextChoices):
    FULL_PAYMENT = "full_payment", "Полный расчёт"
    FULL_PREPAYMENT = "full_prepayment", "Полная предоплата"
    PARTIAL_PREPAYMENT = "partial_prepayment", "Частичная предоплата"
    ADVANCE = "advance", "Аванс"
