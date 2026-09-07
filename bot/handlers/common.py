"""Отрисовка экранов.

Правило: экран всегда **редактируется на месте**, чтобы в чате не рос столбик
из одинаковых меню. Новое сообщение отправляем только когда отредактировать
физически нельзя — под текстом лежит фото или документ, сообщение слишком
старое, или его уже удалили. Тогда старое сносим, чтобы не оставлять мёртвую
клавиатуру, на которую человек будет жать.
"""

import logging
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message

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


async def _send_screen(
    message: Message,
    text: str,
    keyboard: InlineKeyboardMarkup | None,
    photo: Path | None,
) -> Message:
    """Отправить экран новым сообщением — с картинкой, если она есть.

    Не вышло с картинкой — шлём текстом. Экран без иллюстрации хуже, чем с ней,
    но несопоставимо лучше, чем отсутствие экрана.
    """
    if photo is not None and photo.exists():
        try:
            return await message.answer_photo(
                FSInputFile(photo), caption=text, reply_markup=keyboard
            )
        except Exception:
            logger.warning("Не отправилась картинка экрана — показываем текстом", exc_info=True)
    return await message.answer(text, reply_markup=keyboard)


async def render(
    event: Message | CallbackQuery,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
    *,
    force_new: bool = False,
    screen: str | None = None,
    photo: Path | None = None,
) -> int | None:
    """Отрисовать экран и вернуть id сообщения, в котором он оказался.

    Id нужен тем, кто захочет поправить этот экран позже из другого процесса —
    например, вебхуку оплаты. Возвращаем именно фактический: экран мог не
    отредактироваться и уехать в новое сообщение.

    `screen` помечает экран для фоновых задач; любая другая отрисовка метку снимает.
    """
    if isinstance(event, Message):
        mark_screen(event.chat.id, screen)
        sent = await _send_screen(event, text, keyboard, photo)
        return sent.message_id

    message = event.message
    if message is None:
        return None
    mark_screen(message.chat.id, screen)

    # Экран с картинкой всегда уезжает в новое сообщение: текстовое сообщение
    # правкой в сообщение с фото не превратить, `editMessageMedia` работает
    # только там, где медиа уже было.
    can_edit = (
        not force_new
        and photo is None
        and not (message.photo or message.document or message.video)
    )
    if can_edit:
        try:
            await message.edit_text(text, reply_markup=keyboard)
            return message.message_id
        except TelegramBadRequest as exc:
            if NOT_MODIFIED in str(exc):
                # Человек нажал ту же кнопку второй раз — экран уже такой.
                return message.message_id
            logger.debug("Не удалось отредактировать сообщение: %s", exc)

    # Сначала отправляем, потом сносим старое. Обратный порядок однажды стоил
    # нам пропавшего экрана: в тексте была битая разметка, правка не прошла,
    # сообщение удалилось, отправка упала на той же разметке — и у человека
    # осталась пустота вместо меню. Пусть лучше мелькнёт дубль.
    sent = await _send_screen(message, text, keyboard, photo)
    await try_delete(message)
    return sent.message_id
