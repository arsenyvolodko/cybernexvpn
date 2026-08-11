"""Подтягивает NexUser и кладёт его в data, чтобы хендлеры о нём не думали."""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from bot import texts
from bot.services import get_or_create_user

logger = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: User | None = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        try:
            user, created = await get_or_create_user(
                tg_user.id, tg_user.username, tg_user.first_name
            )
        except Exception:
            logger.exception("Не удалось получить пользователя %s", tg_user.id)
            await _reply(event, texts.SOMETHING_WENT_WRONG)
            return None

        data["user"] = user
        data["user_created"] = created
        return await handler(event, data)


async def _reply(event: TelegramObject, text: str) -> None:
    answer = getattr(event, "answer", None)
    if answer is not None:
        try:
            await answer(text)
        except Exception:
            logger.debug("Не смогли ответить пользователю", exc_info=True)
