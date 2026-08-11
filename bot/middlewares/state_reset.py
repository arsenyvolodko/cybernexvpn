"""Сброс незавершённого ввода при любом нажатии кнопки.

Сценарии с ожиданием текста — почта для чека и обращение в поддержку — можно
бросить на полпути: человек просто уходит в другой раздел кнопкой. Без сброса
состояние осталось бы висеть, и следующее его сообщение — хоть «привет» —
улетело бы в поддержку.

Поэтому: любое нажатие кнопки очищает ожидание. Хендлеры, которым состояние
нужно, ставят его сами уже после этого.
"""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import TelegramObject


class StateResetMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        state: FSMContext | None = data.get("state")
        if state is not None and await state.get_state() is not None:
            await state.set_state(None)
        return await handler(event, data)
