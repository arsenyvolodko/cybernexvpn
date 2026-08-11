"""Снять закреплённые сообщения у всех пользователей — разовая уборка.

В старой версии всем закрепляли сообщение со ссылкой на веб-панель. Панели
больше нет, а закреп у людей остался и ведёт в никуда.

Телеграм не даёт снять закреп «оптом»: только по одному чату. Часть людей бота
заблокировала или удалила чат — на них прилетает ошибка, и это нормально,
такие просто считаются в пропущенные.

    python manage.py unpin_all --dry-run
    python manage.py unpin_all
"""

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

from bot.main import build_bot
from nexvpn.models import NexUser

logger = logging.getLogger(__name__)

# Telegram душит рассылки примерно на 30 сообщениях в секунду.
DELAY_BETWEEN_CHATS = 0.05


class Command(BaseCommand):
    help = "Снять все закреплённые сообщения в личных чатах с пользователями."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Только посчитать, кому пошлём")
        parser.add_argument("--limit", type=int, default=0, help="Обработать не больше N чатов")

    def handle(self, *args, **options):
        asyncio.run(self._run(options))

    async def _run(self, options):
        user_ids = await self._get_user_ids(options["limit"])
        self.stdout.write(f"Чатов к обработке: {len(user_ids)}")

        if options["dry_run"]:
            return

        bot = build_bot()
        ok = skipped = 0
        try:
            for index, user_id in enumerate(user_ids, start=1):
                if await self._unpin(bot, user_id):
                    ok += 1
                else:
                    skipped += 1
                if index % 100 == 0:
                    self.stdout.write(f"  ...{index}/{len(user_ids)}")
                await asyncio.sleep(DELAY_BETWEEN_CHATS)
        finally:
            await bot.session.close()

        self.stdout.write(
            self.style.SUCCESS(f"Готово: снято у {ok}, пропущено {skipped} (заблокировали бота или нет чата)")
        )

    async def _unpin(self, bot, user_id: int) -> bool:
        try:
            await bot.unpin_all_chat_messages(chat_id=user_id)
            return True
        except TelegramRetryAfter as exc:
            # Упёрлись в лимит — переждать и повторить один раз.
            await asyncio.sleep(exc.retry_after + 1)
            try:
                await bot.unpin_all_chat_messages(chat_id=user_id)
                return True
            except TelegramAPIError:
                return False
        except TelegramAPIError as exc:
            logger.debug("Не сняли закреп у %s: %s", user_id, exc)
            return False

    @sync_to_async
    def _get_user_ids(self, limit: int) -> list[int]:
        queryset = NexUser.objects.order_by("pk").values_list("pk", flat=True)
        if limit:
            queryset = queryset[:limit]
        return list(queryset)
