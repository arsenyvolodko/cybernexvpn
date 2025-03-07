# Generated by Django 5.1.6 on 2025-03-02 14:39

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nexvpn", "0004_alter_payment_status"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="payment",
            name="status",
        ),
        migrations.AddField(
            model_name="transaction",
            name="status",
            field=models.CharField(
                choices=[
                    ("waiting_for_capture", "Ожидается оплата"),
                    ("succeeded", "Успешный платеж"),
                    ("failed", "Неуспешный платеж"),
                    ("canceled", "Платеж отменен"),
                ],
                default="succeeded",
                max_length=31,
            ),
        ),
    ]
