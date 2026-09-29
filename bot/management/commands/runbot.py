import asyncio
import logging

from django.core.management.base import BaseCommand

from django.conf import settings

from bot.main import run_polling, run_webhook

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Запустить Telegram-бота: вебхуком, если задан TG_WEBHOOK_URL, иначе опросом."

    def handle(self, *args, **options):
        mode = run_webhook if settings.TG_WEBHOOK_URL else run_polling
        logger.info("Режим бота: %s", "вебхук" if settings.TG_WEBHOOK_URL else "опрос")
        try:
            asyncio.run(mode())
        except KeyboardInterrupt:
            self.stdout.write("Остановлен.")
