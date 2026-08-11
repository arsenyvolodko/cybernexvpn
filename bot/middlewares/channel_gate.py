"""Не пускаем новичка дальше, пока он не подписался на канал.

Один только `/start` закрыть недостаточно: человек может написать что угодно
текстом или нажать кнопку под старым сообщением, и попадёт в меню в обход
проверки. Поэтому заслон стоит в middleware и покрывает всё сразу.

Два исключения. Сама кнопка «Я подписался» — иначе проверку не пройти никогда.
И `/start` — его обрабатывает хендлер: там же засчитывается переход по
реферальной ссылке, а он должен случиться до экрана подписки.
"""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot import texts
from bot.channel import CHECK_CALLBACK, gate_keyboard, gate_required_for

logger = logging.getLogger(__name__)


class ChannelGateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("user")
        if user is None or not gate_required_for(user):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            if event.data == CHECK_CALLBACK:
                return await handler(event, data)
            await event.answer()
            if event.message is not None:
                await event.message.answer(texts.CHANNEL_GATE, reply_markup=gate_keyboard())
            return None

        if isinstance(event, Message):
            if await CommandStart()(event):
                return await handler(event, data)
            await event.answer(texts.CHANNEL_GATE, reply_markup=gate_keyboard())
            return None

        return await handler(event, data)
