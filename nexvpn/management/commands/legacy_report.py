"""Отчёт по переносу старой базы: что кому достанется после миграции.

Считает ровно тем же кодом, что и дата-миграция 0022, но ничего не пишет.
Порядок работы: восстановить дамп старой БД локально → прогнать эту команду →
посмотреть глазами и на цифры, и на «хвост» (кому досталось больше всех) →
только потом накатывать миграции на прод.

    python manage.py legacy_report
    python manage.py legacy_report --csv /tmp/legacy.csv --top 20

Прод-БД тут не при чём: команда работает с той базой, что настроена в .env.
"""

import csv
from datetime import date, datetime, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection

from nexvpn.models import Plan
from nexvpn.subscription import legacy
from nexvpn.subscription.plans import DEFAULT_PLAN_PRICES

CSV_COLUMNS = [
    "user_id", "username", "balance", "device_count", "devices_trimmed",
    "created_after_cutoff", "plan_device_limit", "price_month",
    "days_from_balance", "remaining_paid_days", "bonus_days",
    "days_total", "days_granted", "capped", "floored", "unlimited", "notes",
]


class Command(BaseCommand):
    help = "Показать, как старые балансы и устройства превратятся в подписки. Ничего не меняет."

    def add_arguments(self, parser):
        parser.add_argument("--csv", dest="csv_path", default=None, help="Куда выгрузить построчный отчёт")
        parser.add_argument("--top", type=int, default=10, help="Сколько самых крупных начислений показать")
        parser.add_argument(
            "--cutoff", default=settings.LEGACY_CUTOFF_DATE,
            help="Дата среза активных устройств (по умолчанию из настроек)",
        )

    def handle(self, *args, **options):
        cutoff = date.fromisoformat(options["cutoff"])
        today = datetime.now(timezone.utc).date()

        # На дампе старой базы таблицы Plan ещё нет — считаем по стартовой сетке.
        try:
            plan_prices = dict(Plan.objects.values_list("device_limit", "price_month"))
        except DatabaseError:
            plan_prices = {}
        if not plan_prices:
            plan_prices = dict(DEFAULT_PLAN_PRICES)
            self.stdout.write(self.style.WARNING("Тарифов в базе нет — считаю по стартовой сетке."))

        try:
            conversions = legacy.convert_all(connection, plan_prices, cutoff, today)
        except Exception as exc:
            raise CommandError(
                f"Не удалось прочитать легаси-таблицы ({exc}). "
                "Команда рассчитана на базу, где ещё не применена миграция 0023."
            ) from exc

        self._print_summary(conversions, cutoff, plan_prices)
        self._print_top(conversions, options["top"])

        if options["csv_path"]:
            self._write_csv(conversions, options["csv_path"])
            self.stdout.write(self.style.SUCCESS(f"Отчёт записан: {options['csv_path']}"))

    def _print_summary(self, conversions, cutoff, plan_prices):
        total = len(conversions)
        with_devices = [c for c in conversions if c.device_count > 0]
        no_devices = [c for c in conversions if c.device_count == 0]
        capped = [c for c in conversions if c.capped]
        floored = [c for c in conversions if c.floored]
        unlimited = [c for c in conversions if c.unlimited]
        trimmed = [c for c in conversions if c.devices_trimmed]
        late = [c for c in conversions if c.created_after_cutoff]

        self.stdout.write("")
        self.stdout.write(
            f"Дата среза: {cutoff}. "
            f"Начисление зажато в [{settings.LEGACY_MIN_DAYS}, {settings.LEGACY_MAX_DAYS}] дн. "
            f"Безлимит при балансе > {settings.LEGACY_UNLIMITED_BALANCE}₽."
        )
        self.stdout.write(f"Тарифы: " + ", ".join(f"{k} устр. — {v}₽" for k, v in sorted(plan_prices.items())))
        self.stdout.write("")
        self.stdout.write(f"Всего пользователей:            {total}")
        self.stdout.write(f"  с активными устройствами:     {len(with_devices)}")
        self.stdout.write(f"  без устройств (30 дн. даром): {len(no_devices)}")
        self.stdout.write(f"  подняты нижним порогом:       {len(floored)}")
        self.stdout.write(f"  упёрлись в потолок:           {len(capped)}")
        self.stdout.write(f"  безлимит (баланс залит вручную): {len(unlimited)}")
        self.stdout.write(f"  срезаны устройства (>10):     {len(trimmed)}")
        self.stdout.write(f"  есть устройства, заведённые после среза: {len(late)}")
        self.stdout.write("")

        by_plan: dict[int, int] = {}
        for conversion in conversions:
            by_plan[conversion.plan_device_limit] = by_plan.get(conversion.plan_device_limit, 0) + 1
        self.stdout.write("Распределение по тарифам:")
        for limit in sorted(by_plan):
            self.stdout.write(f"  {limit:>2} устр.: {by_plan[limit]}")

        granted = [c.days_granted for c in conversions] or [0]
        balances = [c.balance for c in conversions] or [0]
        self.stdout.write("")
        self.stdout.write(
            f"Дней начислено: всего {sum(granted)}, "
            f"среднее {sum(granted) / len(granted):.1f}, максимум {max(granted)}"
        )
        self.stdout.write(f"Баланс: всего {sum(balances)}₽, максимум {max(balances)}₽")

    def _print_top(self, conversions, top: int):
        if not conversions or top <= 0:
            return
        ranked = sorted(conversions, key=lambda c: c.days_granted, reverse=True)[:top]
        self.stdout.write("")
        self.stdout.write(f"Топ-{len(ranked)} по количеству дней:")
        self.stdout.write(f"{'user_id':>12} {'username':<20} {'₽':>7} {'устр':>5} {'тариф':>6} {'дней':>6}")
        for conversion in ranked:
            self.stdout.write(
                f"{conversion.user_id:>12} {(conversion.username or '—')[:20]:<20} "
                f"{conversion.balance:>7} {conversion.device_count:>5} "
                f"{conversion.plan_device_limit:>6} {conversion.days_granted:>6}"
                + ("  ← безлимит" if conversion.unlimited else "")
                + ("  ← потолок" if conversion.capped else "")
                + ("  ← порог" if conversion.floored else "")
            )

    def _write_csv(self, conversions, path: str):
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_COLUMNS)
            for conversion in conversions:
                writer.writerow(
                    [
                        conversion.user_id, conversion.username or "", conversion.balance,
                        conversion.device_count, conversion.devices_trimmed, conversion.created_after_cutoff,
                        conversion.plan_device_limit, conversion.price_month,
                        conversion.days_from_balance, conversion.remaining_paid_days, conversion.bonus_days,
                        conversion.days_total, conversion.days_granted,
                        "да" if conversion.capped else "",
                        "да" if conversion.floored else "",
                        "да" if conversion.unlimited else "",
                        "; ".join(conversion.notes),
                    ]
                )
