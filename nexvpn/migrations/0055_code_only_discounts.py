"""Две скидки только по промокоду: в меню их нет, получить можно кодом или ссылкой.

Коды случайные — чтобы их нельзя было угадать. Цены — доля от каталожных на
момент миграции, округление вниз. Срок до 01.09.2027. Всё правится в админке.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import migrations

DISCOUNTS = [
    # название, код, надпись «-N%✅», доля цены
    ("Скидка 30%", "NEX30X85N8", "30", 70),
    ("Скидка 50%", "NEX50B26SW", "50", 50),
]


def forward(apps, schema_editor):
    Discount = apps.get_model("nexvpn", "Discount")
    DiscountPrice = apps.get_model("nexvpn", "DiscountPrice")
    Plan = apps.get_model("nexvpn", "Plan")
    plans = list(Plan.objects.filter(is_active=True, is_public=True))
    for title, code, badge, share in DISCOUNTS:
        if Discount.objects.filter(code__iexact=code).exists():
            continue
        discount = Discount.objects.create(
            title=title,
            kind="code",
            code=code,
            valid_until=datetime(2027, 9, 1, tzinfo=ZoneInfo("Europe/Moscow")),
            badge=badge,
            is_active=True,
            is_public=True,
            show_in_menu=False,
        )
        for plan in plans:
            DiscountPrice.objects.create(discount=discount, plan=plan, price_month=plan.price_month * share // 100)


def backward(apps, schema_editor):
    apps.get_model("nexvpn", "Discount").objects.filter(code__in=[code for _, code, _, _ in DISCOUNTS]).delete()


class Migration(migrations.Migration):
    dependencies = [("nexvpn", "0054_discount_code_and_request")]
    operations = [migrations.RunPython(forward, backward)]
