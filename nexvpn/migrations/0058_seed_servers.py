"""Заводим то, что уже работает, чтобы список не начинался с пустой страницы.

Только то, что проверено по панели и по ssh-конфигу: имя, хостер, адрес,
роль и связь с нодой. Цены, лимиты и даты продления не выдумываем — их
проставит владелец в админке, поля для этого есть.

Страна заполнена там, где её отдаёт панель; где панель отвечает XX, оставлено
пусто: лучше пустая клетка, чем выдуманная.
"""

from django.db import migrations

# имя, хостер, адрес, страна, город, роль, нода в панели, заметка
SERVERS = [
    ("eu1-ovh", "OVH", "eu1.pineferry.com", "Франция", "", "exit_abroad", "eu1-ovh", ""),
    ("de1-ovh", "OVH", "de1.pineferry.com", "Германия", "", "exit_abroad", "de1-ovh", ""),
    ("pl1-ovh", "OVH", "pl1.pineferry.com", "Польша", "", "exit_abroad", "pl1-ovh", ""),
    ("de2-ghostnet", "GHOSTnet", "5.175.207.183", "", "", "exit_abroad", "de2-ghostnet", ""),
    ("cs1-cherryservers", "Cherry Servers", "88.216.92.72", "Нидерланды", "", "exit_abroad",
     "cs1-cherryservers", ""),
    ("eu2-alexhost", "AlexHost", "45.93.8.20", "Нидерланды", "", "exit_abroad", "eu2-alexhost",
     "Мёртв: нода не подключается, ECONNREFUSED на порт агента."),
    ("ru1-timeweb", "Timeweb", "62.113.42.120", "Россия", "Москва", "relay_ru", "ru1-timeweb",
     "Двойная роль: вход-релей для зарубежных нод и собственный выход в РФ (инбаунд RU-REALITY:2087)."),
    ("relay-iot", "IOT", "185.71.198.238", "Россия", "", "relay_ru", "",
     "Российский вход, пробрасывает на зарубежные ноды. В панели нодой не заведён."),
    ("nex-prod", "", "176.124.203.129", "", "", "app", "", "Бот, сайт, дашборд, база."),
    ("nex-panel", "OVH", "57.129.123.150", "", "", "panel", "", "Панель Remnawave."),
    ("nex-vds", "VDSina", "138.16.164.160", "Россия", "", "sub_mirror", "",
     "Российское зеркало подписки."),
]


def seed(apps, schema_editor):
    Server = apps.get_model("nexvpn", "Server")
    for name, provider, address, country, city, role, node, note in SERVERS:
        Server.objects.update_or_create(
            name=name,
            defaults={
                "provider": provider,
                "address": address,
                "country": country,
                "city": city,
                "role": role,
                "panel_node_name": node,
                "note": note,
                "is_active": name != "eu2-alexhost",
            },
        )


def unseed(apps, schema_editor):
    Server = apps.get_model("nexvpn", "Server")
    Server.objects.filter(name__in=[row[0] for row in SERVERS]).delete()


class Migration(migrations.Migration):
    dependencies = [("nexvpn", "0057_servers")]
    operations = [migrations.RunPython(seed, unseed)]
