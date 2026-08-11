"""Кнопки под объявлениями из рассылки.

Отличаются от меню одним: **не трогают текст сообщения**. Объявление должно
остаться в чате как было — человек может вернуться и перечитать, что мы там
обещали. Поэтому кнопку с него снимаем, чтобы на неё не жали повторно, а
нужный экран присылаем отдельным сообщением.
"""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot import texts
from bot.broadcast import CONNECT_CALLBACK, MENU_CALLBACK
from bot.notify import PAYMENT_OK_CALLBACK
from bot.handlers.connect import connect_screen
from bot.keyboards import keyboards
from bot.screen_state import mark_screen
from nexvpn.models import NexUser

logger = logging.getLogger(__name__)

router = Router(name="broadcast")


async def _reply_with_screen(call: CallbackQuery, text: str, keyboard) -> None:
    message = call.message
    if message is None:
        return
    # Метку экрана снимаем: фоновые задачи не должны считать, что человек
    # по-прежнему сидит на том экране, откуда пришёл.
    mark_screen(message.chat.id, None)
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:
        # Объявление могли удалить или оно слишком старое — не повод не
        # показать человеку то, за чем он нажал.
        logger.debug("Не удалось снять клавиатуру с объявления", exc_info=True)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == CONNECT_CALLBACK)
async def handle_connect_from_broadcast(call: CallbackQuery, user: NexUser) -> None:
    await call.answer()
    text, keyboard = await connect_screen(user)
    await _reply_with_screen(call, text, keyboard)


@router.callback_query(F.data == MENU_CALLBACK)
async def handle_menu_from_broadcast(call: CallbackQuery) -> None:
    await call.answer()
    await _reply_with_screen(call, texts.MAIN_MENU, keyboards.main_menu())


@router.callback_query(F.data == PAYMENT_OK_CALLBACK)
async def handle_payment_ok(call: CallbackQuery) -> None:
    """«Отлично» под сообщением об оплате.

    Ведёт себя как кнопки объявлений: снимает клавиатуру с сообщения об оплате
    и присылает меню отдельным сообщением. Само сообщение об оплате не трогаем —
    это чек, к нему человек может вернуться.
    """
    await call.answer()
    await _reply_with_screen(call, texts.MAIN_MENU, keyboards.main_menu())
