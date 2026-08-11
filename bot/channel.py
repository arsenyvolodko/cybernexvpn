"""Проверка подписки на канал.

Правило простое: спрашиваем только у новичков. У легаси доступ уже оплачен, и
ставить им условие задним числом было бы нечестно.

Отдельно про надёжность. Если Telegram не ответил или бота вывели из
администраторов канала, проверить подписку нечем — и тогда мы **пропускаем**
человека дальше. Потерять регистрацию из-за нашей же поломки хуже, чем
пропустить одного неподписавшегося.
"""

import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.conf import settings

logger = logging.getLogger(__name__)

CHECK_CALLBACK = "channel_check"

# «restricted» тоже считаем подпиской: человек в канале, просто ограничен.
SUBSCRIBED_STATUSES = {"creator", "administrator", "member", "restricted"}


def gate_enabled() -> bool:
    return bool(settings.TG_CHANNEL_USERNAME)


def gate_required_for(user) -> bool:
    return gate_enabled() and not user.is_legacy and not user.joined_channel


def gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти в канал 📢", url=settings.TG_CHANNEL_URL)],
            [InlineKeyboardButton(
                text="Я подписался ✅", callback_data=CHECK_CALLBACK, style="success"
            )],
        ]
    )


async def is_subscribed(bot, user_id: int) -> bool:
    """Состоит ли человек в канале. При любой ошибке отвечаем «да»."""
    try:
        member = await bot.get_chat_member(settings.TG_CHANNEL_USERNAME, user_id)
    except TelegramAPIError as exc:
        logger.warning("Не смогли проверить подписку %s: %s — пропускаем", user_id, exc)
        return True
    return member.status in SUBSCRIBED_STATUSES
