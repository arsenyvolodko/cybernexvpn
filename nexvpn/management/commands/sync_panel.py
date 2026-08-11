"""Разослать подписки в Remnawave вручную, не дожидаясь celery.

Нужно сразу после миграции старой базы: дата-миграция намеренно не ходит в сеть
и оставляет все подписки в статусе PENDING. Обычно их разбирает периодическая
задача `sync_panel`, но если celery ещё не поднят — пользователи не получат
доступ, пока это не выполнить.

    python manage.py sync_panel            # только то, что не синхронизировано
    python manage.py sync_panel --all      # переписать состояние всех подписок
    python manage.py sync_panel --dry-run  # показать, что было бы отправлено

С DEBUG=True команда откажется работать. Причина не теоретическая: локальный
`.env` смотрит на боевую панель, и один запуск с дев-базы залил туда 269
пользователей с чужими датами. Единичные операции ботом при отладке нужны и
разрешены, а вот массовая заливка с машины разработчика — никогда.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from nexvpn.enums import PanelSyncStatusEnum
from nexvpn.models import Subscription
from nexvpn.remnawave import RemnawaveClient, RemnawaveError
from nexvpn.subscription import panel_sync


class Command(BaseCommand):
    help = "Привести пользователей в Remnawave в соответствие с подписками."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Включая уже синхронизированные")
        parser.add_argument("--dry-run", action="store_true", help="Ничего не отправлять")
        parser.add_argument("--limit", type=int, default=0, help="Обработать не больше N подписок")
        parser.add_argument(
            "--i-know-this-is-production",
            action="store_true",
            help="Разрешить запуск при DEBUG=True (панель в .env боевая)",
        )

    def handle(self, *args, **options):
        writes = not options["dry_run"]
        if writes and settings.DEBUG and not options["i_know_this_is_production"]:
            raise CommandError(
                "DEBUG=True — похоже, это локальная машина, а PANEL_API_URL смотрит на боевую "
                f"панель ({settings.PANEL_API_URL}).\nМассовая заливка отсюда уже однажды создала "
                "там 269 лишних пользователей.\nЕсли точно нужно — добавьте "
                "--i-know-this-is-production, а посмотреть без записи можно через --dry-run."
            )

        queryset = Subscription.objects.select_related("user", "plan").order_by("pk")
        if not options["all"]:
            queryset = queryset.exclude(panel_status=PanelSyncStatusEnum.SYNCED)
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        total = queryset.count() if not options["limit"] else len(queryset)
        self.stdout.write(f"К отправке: {total}")
        if not total:
            return

        if options["dry_run"]:
            for subscription in queryset:
                self.stdout.write(
                    f"  {subscription.user.panel_username}: "
                    f"hwidDeviceLimit={subscription.device_limit}, "
                    f"expireAt={subscription.expires_at:%Y-%m-%d}"
                )
            return

        client = RemnawaveClient()
        ok = failed = 0
        for subscription in queryset:
            try:
                success = panel_sync.sync_subscription(subscription, client=client)
            except RemnawaveError as exc:
                self.stderr.write(f"  {subscription.user.panel_username}: {exc}")
                success = False
            ok, failed = (ok + 1, failed) if success else (ok, failed + 1)
            if (ok + failed) % 50 == 0:
                self.stdout.write(f"  ...{ok + failed}/{total}")

        style = self.style.SUCCESS if not failed else self.style.WARNING
        self.stdout.write(style(f"Готово: успешно {ok}, с ошибкой {failed}"))
        if failed:
            self.stdout.write("Неудачные останутся в статусе FAILED — можно перезапустить команду.")
