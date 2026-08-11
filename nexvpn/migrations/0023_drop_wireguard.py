"""Шаг 3/3 миграции на VLESS: сносим таблицы WireGuard и баланс.

Отдельной миграцией, а не вместе с 0021, потому что 0022 читает эти таблицы.
Что было у каждого пользователя до сноса, остаётся в `LegacyMigrationRecord` —
после дропа это единственный способ объяснить, откуда взялись стартовые дни.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nexvpn", "0022_legacy_data"),
    ]

    operations = [
        migrations.RemoveField(model_name="endpoint", name="client"),
        migrations.RemoveField(model_name="endpoint", name="server"),
        migrations.RemoveField(model_name="clientupdates", name="client"),
        migrations.RemoveField(model_name="clientupdates", name="user"),
        migrations.RemoveField(model_name="server", name="config"),
        migrations.RemoveField(model_name="userbalance", name="user"),
        migrations.DeleteModel(name="Client"),
        migrations.DeleteModel(name="ClientUpdates"),
        migrations.DeleteModel(name="Endpoint"),
        migrations.DeleteModel(name="Server"),
        migrations.DeleteModel(name="ServerConfig"),
        migrations.DeleteModel(name="UserBalance"),
    ]
