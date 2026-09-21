"""Кнопки под сообщениями, которые нельзя переписывать.

Объявление из рассылки, чек об оплате, картинка с инструкцией по выбору
туннеля — всё это должно остаться в чате как было: человек может вернуться и
перечитать. Поэтому такие кнопки **не трогают текст сообщения**: клавиатуру с
него снимаем, чтобы на неё не жали повторно, а нужный экран присылаем
отдельным сообщением.
"""

import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, PollAnswer
from django.conf import settings

from bot import texts
from bot.broadcast import (
    CONNECT_CALLBACK,
    MENU_CALLBACK,
    MENU_KEEP_REFERRAL_CALLBACK,
    awaiting_custom_answer,
    custom_prompt_keyboard,
    decline_custom_answer,
    menu_keyboard,
    record_poll_answer,
    save_custom_answer,
)
from bot.keyboards.factories import PollCustomCallback
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


@router.poll_answer()
async def handle_poll_answer(answer: PollAnswer, bot: Bot) -> None:
    """Голос в опросе из рассылки. Приходит, только если опрос неанонимный."""
    result = await record_poll_answer(answer.poll_id, answer.option_ids)
    if not result.known:
        logger.info("Голос в незнакомом опросе %s", answer.poll_id)
        return
    if result.ask_custom:
        try:
            await bot.send_message(
                result.user_id,
                texts.POLL_CUSTOM_PROMPT,
                reply_markup=custom_prompt_keyboard(result.broadcast_id),
            )
        except Exception:
            logger.warning("Не удалось попросить свой вариант у %s", result.user_id, exc_info=True)


@router.callback_query(PollCustomCallback.filter())
async def handle_custom_decline(call: CallbackQuery, callback_data: PollCustomCallback) -> None:
    """«Не буду отвечать»: снимаем кнопку и благодарим."""
    await call.answer()
    message = call.message
    if message is not None:
        try:
            await message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("Не удалось снять кнопку с просьбы о своём варианте", exc_info=True)
    declined = await decline_custom_answer(call.from_user.id, callback_data.broadcast_id)
    # Не от чего отказываться — ответ уже прислан. Благодарить второй раз незачем.
    if declined and message is not None:
        await message.answer(texts.POLL_CUSTOM_DECLINED, reply_markup=menu_keyboard())


# --- ответ «своим вариантом» ---
#
# Отдельный роутер, подключается в самом начале: следующее сообщение человека
# после просьбы — это его ответ, и ни один другой сценарий не должен его
# перехватить. Кроме тех, в которых человек сам сейчас что-то вводит
# (поддержка, email): там у него есть состояние, и сюда он не попадёт.

custom_answer_router = Router(name="poll_custom_answer")

MAX_CUSTOM_ANSWER = 3000


async def _pending_custom_answer(message: Message) -> dict | bool:
    """Фильтр: ждём ли от автора «свой вариант».

    Пользователя берём из сообщения, а не из UserMiddleware: middleware в боте
    внутренние и выполняются уже после фильтров.
    """
    if message.from_user is None or (message.text or "").startswith("/"):
        return False
    if message.from_user.id == settings.TG_ADMIN_USER_ID and message.reply_to_message:
        # Реплай администратора — это ответ на обращение, его несёт support.
        return False
    vote = await awaiting_custom_answer(message.from_user.id)
    return {"pending_vote": vote} if vote is not None else False


@custom_answer_router.message(StateFilter(None), _pending_custom_answer)
async def handle_custom_answer(message: Message, pending_vote) -> None:
    text = (message.text or message.caption or "").strip()
    if not text:
        await message.answer(texts.POLL_CUSTOM_EMPTY)
        return
    await save_custom_answer(pending_vote.pk, text[:MAX_CUSTOM_ANSWER])

    sender = message.from_user
    if settings.TG_ADMIN_USER_ID:
        try:
            await message.bot.send_message(
                settings.TG_ADMIN_USER_ID,
                texts.POLL_CUSTOM_TO_ADMIN.format(
                    title=html.escape(pending_vote.broadcast.title),
                    name=html.escape(sender.full_name),
                    username=f" (@{sender.username})" if sender.username else "",
                    user_id=sender.id,
                    text=html.escape(text[:MAX_CUSTOM_ANSWER]),
                ),
            )
        except Exception:
            # Ответ всё равно сохранён — он виден в результатах опроса в админке.
            logger.exception("Не удалось переслать свой вариант от %s", sender.id)
    else:
        logger.error("Свой вариант некуда переслать: не задан TG_ADMIN_USER_ID")

    await message.answer(texts.POLL_CUSTOM_THANKS, reply_markup=menu_keyboard())
