"""Отрисовка экранов.

Правило: экран всегда **редактируется на месте**, чтобы в чате не рос столбик
из одинаковых меню. Новое сообщение отправляем только когда отредактировать
физически нельзя — под текстом лежит фото или документ, сообщение слишком
старое, или его уже удалили. Тогда старое сносим, чтобы не оставлять мёртвую
клавиатуру, на которую человек будет жать.
"""

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.screen_state import mark_screen

logger = logging.getLogger(__name__)

NOT_MODIFIED = "message is not modified"


async def try_delete(message: Message) -> bool:
    try:
        await message.delete()
        return True
    except Exception:
        # Сообщение старше 48 часов или уже удалено — снимаем хотя бы клавиатуру,
        # чтобы не осталось кнопок, которые ничего не делают.
        try:
            await message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("Не удалось ни удалить сообщение, ни снять клавиатуру", exc_info=True)
        return False


async def render(
    event: Message | CallbackQuery,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
    *,
    force_new: bool = False,
    screen: str | None = None,
) -> None:
    """`screen` помечает экран для фоновых задач; любая другая отрисовка метку снимает."""
    if isinstance(event, Message):
        mark_screen(event.chat.id, screen)
        await event.answer(text, reply_markup=keyboard)
        return

    message = event.message
    if message is None:
        return
    mark_screen(message.chat.id, screen)

    can_edit = not force_new and not (message.photo or message.document or message.video)
    if can_edit:
        try:
            await message.edit_text(text, reply_markup=keyboard)
            return
        except TelegramBadRequest as exc:
            if NOT_MODIFIED in str(exc):
                # Человек нажал ту же кнопку второй раз — экран уже такой.
                return
            logger.debug("Не удалось отредактировать сообщение: %s", exc)

    # Сначала отправляем, потом сносим старое. Обратный порядок однажды стоил
    # нам пропавшего экрана: в тексте была битая разметка, правка не прошла,
    # сообщение удалилось, отправка упала на той же разметке — и у человека
    # осталась пустота вместо меню. Пусть лучше мелькнёт дубль.
    await message.answer(text, reply_markup=keyboard)
    await try_delete(message)
