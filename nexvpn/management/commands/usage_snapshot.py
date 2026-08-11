"""Снять срез использования руками, не дожидаясь celery.

Полезно сразу после выката: первый снимок — это точка отсчёта, прирост трафика
считается только со второго. Без него первые десять минут админка пустая.
"""

from django.core.management.base import BaseCommand

from nexvpn import telemetry


class Command(BaseCommand):
    help = "Снять срез использования из панели"

    def handle(self, *args, **options):
        result = telemetry.take_snapshot()
        self.stdout.write(
            f"Пользователей в срезе: {result.seen}, онлайн: {result.online}, "
            f"прирост трафика: {result.traffic_delta} Б, "
            f"нет в нашей базе: {result.unknown_users}"
        )
        silent = telemetry.silent_users()
        if silent:
            never = sum(1 for row in silent if row["never_connected"])
            self.stdout.write(
                self.style.WARNING(
                    f"Молчат при живой подписке: {len(silent)}, из них ни разу не подключались: {never}"
                )
            )
