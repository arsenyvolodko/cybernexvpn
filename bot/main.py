"""Точка сборки бота. Запускается через `manage.py runbot`."""

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, ErrorEvent
from django.conf import settings

from bot.handlers import build_router
from bot.middlewares.maintenance import MaintenanceMiddleware
from bot.middlewares.state_reset import StateResetMiddleware
from bot.middlewares.user import UserMiddleware

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Запустить бота"),
    BotCommand(command="menu", description="Меню"),
]


def build_bot() -> Bot:
    if not settings.TG_BOT_TOKEN:
        raise RuntimeError("Не задан TG_BOT_TOKEN")
    return Bot(
        token=settings.TG_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


STALE_QUERY_MARKERS = ("query is too old", "query ID is invalid")


async def handle_error(event: ErrorEvent) -> bool:
    """Гасим шум от протухших callback'ов.

    Пока бот перезапускался, люди жали кнопки. Их запросы прилетают пачкой,
    и Telegram уже не принимает на них ответ. Это не ошибка приложения, а
    нормальная жизнь при деплое — незачем засорять лог трейсбеками, за которыми
    не видно настоящих проблем.
    """
    exception = event.exception
    if isinstance(exception, TelegramBadRequest) and any(
        marker in str(exception) for marker in STALE_QUERY_MARKERS
    ):
        logger.debug("Протухший callback: %s", exception)
        return True

    logger.exception("Необработанная ошибка в боте", exc_info=exception)
    return True


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.errors.register(handle_error)

    # Порядок важен: сначала техработы (заглушка вместо любого сценария),
    # потом подтягивание пользователя. Наоборот — значит создавать юзеров
    # во время техработ.
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.middleware(MaintenanceMiddleware())
        observer.middleware(UserMiddleware())

    # Нажал кнопку — значит передумал вводить текст. Только для кнопок:
    # у сообщений состояние как раз и есть то, ради чего они пришли.
    dispatcher.callback_query.middleware(StateResetMiddleware())

    dispatcher.include_router(build_router())
    return dispatcher


async def run_polling() -> None:
    bot = build_bot()
    dispatcher = build_dispatcher()

    me = await bot.get_me()
    logger.info("Бот запущен: @%s (id=%s)", me.username, me.id)

    await bot.set_my_commands(COMMANDS)
    # Вебхук мог остаться от прежней версии — без этого polling не получит
    # ни одного апдейта и будет молча простаивать.
    await bot.delete_webhook(drop_pending_updates=False)

    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await bot.session.close()
