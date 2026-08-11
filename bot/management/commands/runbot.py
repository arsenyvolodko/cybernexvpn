import asyncio
import logging

from django.core.management.base import BaseCommand

from bot.main import run_polling

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Запустить Telegram-бота (long polling)."

    def handle(self, *args, **options):
        try:
            asyncio.run(run_polling())
        except KeyboardInterrupt:
            self.stdout.write("Остановлен.")
