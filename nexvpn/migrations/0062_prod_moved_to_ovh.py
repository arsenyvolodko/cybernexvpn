"""Прод переехал с Aeza на OVH — разводим их в списке серверов.

Просто поменять адрес у записи `nex-prod` было бы неверно: к ней привязана
история расходов на Aeza, и после подмены эти деньги оказались бы записаны на
машину, которая тогда ещё не работала. Поэтому старая запись остаётся со своими
расходами и уходит из используемых, а новая заводится рядом.

Цену и дату продления новой машины не выдумываем — их проставит владелец, поля
для этого есть. Запись про Aeza не удаляем: пока за неё платят, она должна быть
видна в деньгах.
"""

from django.db import migrations

OLD = "nex-prod"
NEW = "nex-prod-ovh"


def split(apps, schema_editor):
    Server = apps.get_model("nexvpn", "Server")

    old = Server.objects.filter(name=OLD).first()
    if old and "176.124" in (old.address or ""):
        old.name = "nex-prod-aeza (выведен)"
        old.provider = old.provider or "Aeza"
        old.is_active = False
        old.note = (
            "Старый прод. Выведен 29.09.2026: адрес перестал открываться из российских сетей. "
            "Бот и celery погашены, postgres оставлен для откатa. Запись оставлена ради "
            "истории расходов — удалять после прекращения оплаты."
        )
        old.save()

    Server.objects.update_or_create(
        name=NEW,
        defaults={
            "provider": "OVH",
            "address": "57.131.195.67",
            "country": "",
            "city": "",
            "role": "app",
            "panel_node_name": "",
            "is_active": True,
            "note": "Бот, сайт, дашборд, база. Домен cybernexapp.com. Цену и дату продления заполнить.",
        },
    )


def back(apps, schema_editor):
    """Откат: возвращаем старую запись в работу, новую убираем."""
    Server = apps.get_model("nexvpn", "Server")
    Server.objects.filter(name=NEW).delete()
    old = Server.objects.filter(name="nex-prod-aeza (выведен)").first()
    if old:
        old.name = OLD
        old.is_active = True
        old.save()


class Migration(migrations.Migration):
    dependencies = [("nexvpn", "0061_sub_host_probe")]
    operations = [migrations.RunPython(split, back)]
