"""Кнопки под сообщениями, которые нельзя переписывать.

Объявление из рассылки, чек об оплате, картинка с инструкцией по выбору
туннеля — всё это должно остаться в чате как было: человек может вернуться и
перечитать. Поэтому такие кнопки **не трогают текст сообщения**: клавиатуру с
него снимаем, чтобы на неё не жали повторно, а нужный экран присылаем
отдельным сообщением.
"""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from bot import texts
from bot.broadcast import CONNECT_CALLBACK, MENU_CALLBACK, MENU_KEEP_REFERRAL_CALLBACK
from bot.notify import PAYMENT_OK_CALLBACK
from bot.handlers.connect import connect_screen
from bot.keyboards import keyboards
from bot.keyboards.storage import ButtonsStorage
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


@router.callback_query(F.data == MENU_KEEP_REFERRAL_CALLBACK)
async def handle_menu_keeping_referral(call: CallbackQuery) -> None:
    """«В меню» у рассылки с кнопками рефералки.

    В отличие от обычной снимает с объявления только саму себя: «Поделиться»
    и «Скопировать ссылку» остаются — ради них объявление и присылали.
    """
    await call.answer()
    message = call.message
    if message is None:
        return
    mark_screen(message.chat.id, None)
    rows = [
        row
        for row in (message.reply_markup.inline_keyboard if message.reply_markup else [])
        if not any(button.callback_data == MENU_KEEP_REFERRAL_CALLBACK for button in row)
    ]
    try:
        await message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
        )
    except Exception:
        logger.debug("Не удалось снять «В меню» с объявления", exc_info=True)
    await message.answer(texts.MAIN_MENU, reply_markup=keyboards.main_menu())


@router.callback_query(F.data == PAYMENT_OK_CALLBACK)
async def handle_payment_ok(call: CallbackQuery) -> None:
    """«Отлично» под сообщением об оплате.

    Ведёт себя как кнопки объявлений: снимает клавиатуру с сообщения об оплате
    и присылает меню отдельным сообщением. Само сообщение об оплате не трогаем —
    это чек, к нему человек может вернуться.
    """
    await call.answer()
    await _reply_with_screen(call, texts.MAIN_MENU, keyboards.main_menu())


@router.callback_query(F.data == ButtonsStorage.CONNECT_OK.callback)
async def handle_connect_ok(call: CallbackQuery) -> None:
    """«Хорошо» под картинкой о выборе туннеля.

    Картинку оставляем в чате: на неё человек будет оглядываться, когда полезет
    переключать туннель. Снимаем только клавиатуру и присылаем меню отдельным
    сообщением — с этого момента он снова в обычной навигации.
    """
    await call.answer()
    await _reply_with_screen(call, texts.MAIN_MENU, keyboards.main_menu())
