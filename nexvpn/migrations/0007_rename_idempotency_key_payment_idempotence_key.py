# Generated by Django 5.1.6 on 2025-03-03 10:48

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nexvpn", "0006_alter_transaction_status"),
    ]

    operations = [
        migrations.RenameField(
            model_name="payment",
            old_name="idempotency_key",
            new_name="idempotence_key",
        ),
    ]
