"""Надпись «-30%✅» у студенческой скидки и новая скидка для студентов ЦУ.

Цены ЦУ — 50% от каталожных на момент миграции, округление вниз. Срок, как у
студенческой, до 01.09.2027 — правится в админке.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import migrations

STUDENT = "Скидка для студентов 30%"
CU = "Скидка для студентов ЦУ -50%"
CU_PROMPT = (
    "Отправь, пожалуйста, подтверждение того, что учишься в ЦУ — например, студенческий, "
    "скриншот из личного кабинета или любое другое. Мы сообщим тебе, когда проверим."
)


def forward(apps, schema_editor):
    Discount = apps.get_model("nexvpn", "Discount")
    DiscountPrice = apps.get_model("nexvpn", "DiscountPrice")
    Plan = apps.get_model("nexvpn", "Plan")

    Discount.objects.filter(title=STUDENT, badge="").update(badge="30")

    if Discount.objects.filter(title=CU).exists():
        return
    discount = Discount.objects.create(
        title=CU,
        kind="verification",
        code="",
        valid_until=datetime(2027, 9, 1, tzinfo=ZoneInfo("Europe/Moscow")),
        badge="50",
        is_active=True,
        is_public=True,
        show_in_menu=True,
        verification_prompt=CU_PROMPT,
    )
    for plan in Plan.objects.filter(is_active=True, is_public=True):
        DiscountPrice.objects.create(discount=discount, plan=plan, price_month=plan.price_month * 50 // 100)


def backward(apps, schema_editor):
    Discount = apps.get_model("nexvpn", "Discount")
    Discount.objects.filter(title=CU).delete()
    Discount.objects.filter(title=STUDENT, badge="30").update(badge="")


class Migration(migrations.Migration):
    dependencies = [("nexvpn", "0052_discount_badge")]
    operations = [migrations.RunPython(forward, backward)]
