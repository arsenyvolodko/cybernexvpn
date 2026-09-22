"""Скидка для студентов 30% — кнопка в «Промокоды и скидки».

Цены — 70% от каталожных на момент миграции, округление вниз. Срок до
01.09.2027 выбран как стартовый — правится в админке, как и цены.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import migrations

TITLE = "Скидка для студентов 30%"
PROMPT = (
    "Отправь, пожалуйста, любое подтверждение того, что являешься студентом — например, "
    "студенческий, подтверждение на Госуслугах или любое другое. Мы сообщим тебе, когда проверим."
)


def create(apps, schema_editor):
    Discount = apps.get_model("nexvpn", "Discount")
    DiscountPrice = apps.get_model("nexvpn", "DiscountPrice")
    Plan = apps.get_model("nexvpn", "Plan")
    if Discount.objects.filter(title=TITLE).exists():
        return
    discount = Discount.objects.create(
        title=TITLE,
        kind="verification",
        code="",
        valid_until=datetime(2027, 9, 1, tzinfo=ZoneInfo("Europe/Moscow")),
        is_active=True,
        is_public=True,
        show_in_menu=True,
        verification_prompt=PROMPT,
    )
    for plan in Plan.objects.filter(is_active=True, is_public=True):
        DiscountPrice.objects.create(discount=discount, plan=plan, price_month=plan.price_month * 70 // 100)


def remove(apps, schema_editor):
    apps.get_model("nexvpn", "Discount").objects.filter(title=TITLE).delete()


class Migration(migrations.Migration):
    dependencies = [("nexvpn", "0050_discounts")]
    operations = [migrations.RunPython(create, remove)]
