# Generated by Django 5.1.6 on 2025-03-03 10:57

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nexvpn", "0007_rename_idempotency_key_payment_idempotence_key"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="payment",
            name="user",
        ),
    ]
