"""Режим технических работ.

Флаг лежит в БД, но ходить туда на каждое нажатие кнопки незачем: он меняется
раз в месяц, а кликов тысячи. Держим значение в памяти процесса и обновляем не
чаще раза в минуту — задержка до минуты между «включил в админке» и «бот встал»
приемлема, лишний SELECT на каждый апдейт — нет.

Администратор по умолчанию заглушку не видит: кто-то должен иметь возможность
проверить, что починилось, не выключая режим для всех. Поведение переключается
в админке галкой «Заглушка и для админа».
"""

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from django.conf import settings

from bot import texts
from bot.services import get_maintenance

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60

# Telegram обрезает alert по 200 символам — молча, вместе с концом фразы.
ALERT_LIMIT = 200


def _fit_alert(text: str) -> str:
    if len(text) <= ALERT_LIMIT:
        return text
    return text[: ALERT_LIMIT - 1].rstrip() + "…"


class MaintenanceState:
    """Кеш флага. Один экземпляр на процесс."""

    def __init__(self, ttl: int = CACHE_TTL_SECONDS):
        self._ttl = ttl
        self._checked_at: float = 0.0
        self._enabled: bool = False
        self._affects_admin: bool = False
        self._message: str = texts.MAINTENANCE_FALLBACK
        self._until = None

    async def refresh_if_stale(self) -> None:
        if time.monotonic() - self._checked_at <= self._ttl:
            return
        try:
            state = await get_maintenance()
            self._enabled = state.enabled
            self._affects_admin = state.affects_admin
            self._message = state.message or texts.MAINTENANCE_FALLBACK
            self._until = state.until
        except Exception:
            # БД недоступна — не роняем бота и не блокируем всех молча.
            logger.exception("Не удалось прочитать флаг техработ")
        self._checked_at = time.monotonic()

    async def blocks(self, user_id: int | None) -> tuple[bool, str]:
        await self.refresh_if_stale()
        if not self._enabled:
            return False, ""

        is_admin = bool(settings.TG_ADMIN_USER_ID) and user_id == settings.TG_ADMIN_USER_ID
        if is_admin and not self._affects_admin:
            return False, ""

        text = self._message
        if self._until is not None:
            text += texts.MAINTENANCE_UNTIL.format(until=self._until.strftime("%d.%m %H:%M"))
        return True, text

    def invalidate(self) -> None:
        self._checked_at = 0.0


class MaintenanceMiddleware(BaseMiddleware):
    def __init__(self, state: MaintenanceState | None = None):
        self.state = state or MaintenanceState()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        blocked, text = await self.state.blocks(tg_user.id if tg_user else None)
        if not blocked:
            return await handler(event, data)

        # Заглушка вместо любого сценария. Оплату это не трогает: вебхук
        # YooKassa приходит в Django, а не сюда.
        if isinstance(event, CallbackQuery):
            # Всплывашкой, а не сообщением: экран остаётся на месте, а в чате не
            # копится столбик одинаковых предупреждений от каждого нажатия.
            await event.answer(_fit_alert(text), show_alert=True)
        elif isinstance(event, Message):
            # У команд всплывашки нет — тут только сообщением.
            await event.answer(text)
        return None
