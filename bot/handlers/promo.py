"""Промокоды и скидки.

Два пути к скидке. Промокод — человек вводит код (или приходит по ссылке
`?start=promo_КОД`, это в menu.handle_start), скидка действует сразу.
Скидка с подтверждением — кнопка с её названием: человек присылает
доказательство, бот пересылает его администратору с кнопками
«Подтвердить» / «Отклонить».

Подтверждение — одна отправка: одно сообщение или один альбом. Альбом
Telegram присылает несколькими сообщениями с общим `media_group_id`, поэтому
его части пересылаем все, а всё прочее после первой отправки уже не
подтверждение — оно уходит в обычные сценарии бота.
"""

import html
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from django.conf import settings
from django.utils.timezone import localtime, now

from bot import texts
from bot.broadcast import menu_keyboard
from bot.handlers.common import render
from bot.keyboards import ButtonsStorage, keyboards
from bot.keyboards.factories import DiscountCallback, DiscountReviewCallback
from bot.services import (
    apply_promo_code,
    decide_discount_request,
    discount_request_is_pending,
    discount_request_state,
    get_menu_discount,
    get_promo_screen,
    open_discount_request,
)
from nexvpn.models import NexUser

logger = logging.getLogger(__name__)

router = Router(name="promo")


class PromoForm(StatesGroup):
    waiting_code = State()
    waiting_proof = State()


def _title(discount) -> str:
    return html.escape(discount.title)


async def promo_screen(user: NexUser) -> tuple[str, object]:
    screen = await get_promo_screen(user)
    text = texts.PROMO_MENU
    if screen.active is not None:
        text += texts.PROMO_MENU_ACTIVE.format(
            title=_title(screen.active.discount),
            until=localtime(screen.active.discount.valid_until).strftime("%d.%m.%Y"),
        )
    for discount in screen.pending:
        text += texts.PROMO_MENU_PENDING.format(title=_title(discount))
    return text, keyboards.promo_menu(screen.menu_discounts)


@router.callback_query(F.data == ButtonsStorage.PROMO.callback)
async def handle_promo_menu(call: CallbackQuery, user: NexUser, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    text, keyboard = await promo_screen(user)
    await render(call, text, keyboard)


# --- ввод промокода ---


@router.callback_query(F.data == ButtonsStorage.ENTER_PROMO.callback)
async def handle_enter_promo(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(PromoForm.waiting_code)
    await render(call, texts.PROMO_ENTER, keyboards.back_to_promo())


@router.message(PromoForm.waiting_code)
async def handle_promo_code(message: Message, user: NexUser, state: FSMContext) -> None:
    result = await apply_promo_code(user, message.text or "")
    if result.discount is None:
        # Состояние не сбрасываем: человек может просто опечататься.
        text = texts.PROMO_UNAVAILABLE if result.status == "unavailable" else texts.PROMO_NOT_FOUND
        await message.answer(text, reply_markup=keyboards.back_to_promo())
        return
    await state.clear()
    await message.answer(texts.PROMO_APPLIED.format(title=_title(result.discount)), reply_markup=menu_keyboard())


# --- скидка с подтверждением ---


@router.callback_query(DiscountCallback.filter())
async def handle_discount_button(
    call: CallbackQuery, callback_data: DiscountCallback, user: NexUser, state: FSMContext
) -> None:
    await call.answer()
    discount = await get_menu_discount(user, callback_data.discount_id)
    if discount is None:
        # Скидку выключили или она истекла, пока экран висел.
        text, keyboard = await promo_screen(user)
        await render(call, text, keyboard)
        return

    status = await discount_request_state(user, discount)
    if status == "active":
        await render(call, texts.DISCOUNT_ALREADY_ACTIVE.format(title=_title(discount)), keyboards.back_to_promo())
        return

    await state.set_state(PromoForm.waiting_proof)
    text = html.escape(discount.verification_prompt)
    data = {"discount_id": discount.pk}
    if status == "pending":
        # Заявка висит — всё равно принимаем подтверждение, оно заменит старое.
        # Раньше тут был отказ без режима приёма, и присланное фото получало
        # в ответ просто меню.
        text += texts.DISCOUNT_RESUBMIT_NOTE
        data["replace_before"] = now().isoformat()
    await state.update_data(**data)
    await render(call, text, keyboards.back_to_promo())


@router.message(PromoForm.waiting_proof)
async def handle_discount_proof(message: Message, user: NexUser, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("request_id") is not None:
        # Отправка уже была. Дальше ждём только остальные части того же альбома.
        album = data.get("media_group_id")
        if album is None or message.media_group_id != album:
            await state.clear()
            raise SkipHandler()  # это уже не подтверждение — пусть разберут другие сценарии
        if not await discount_request_is_pending(data["request_id"]):
            await state.clear()
            return

    discount = await get_menu_discount(user, data.get("discount_id") or 0)
    if discount is None:
        await state.clear()
        return

    replace_before = data.get("replace_before")
    request, created = await open_discount_request(
        user, discount, datetime.fromisoformat(replace_before) if replace_before else None
    )
    await state.update_data(request_id=request.pk, media_group_id=message.media_group_id)

    admin_id = settings.TG_ADMIN_USER_ID
    if not admin_id:
        logger.error("Заявку на скидку некуда переслать: не задан TG_ADMIN_USER_ID")
    else:
        sender = message.from_user
        try:
            if created:
                await message.bot.send_message(
                    admin_id,
                    texts.DISCOUNT_REQUEST_TO_ADMIN.format(
                        title=_title(discount),
                        name=html.escape(sender.full_name),
                        username=f" (@{sender.username})" if sender.username else "",
                        user_id=user.pk,
                    ),
                    reply_markup=keyboards.discount_review(request.pk),
                )
            await message.forward(admin_id)
        except Exception:
            logger.exception("Не удалось переслать подтверждение скидки от %s", user.pk)

    if created:
        await message.answer(texts.DISCOUNT_REQUEST_RECEIVED, reply_markup=menu_keyboard())


# --- решение администратора ---


@router.callback_query(DiscountReviewCallback.filter(), F.from_user.id == settings.TG_ADMIN_USER_ID)
async def handle_discount_review(call: CallbackQuery, callback_data: DiscountReviewCallback) -> None:
    request = await decide_discount_request(callback_data.request_id, callback_data.approve)
    if request is None:
        await call.answer(texts.DISCOUNT_ADMIN_ALREADY, show_alert=True)
        return
    await call.answer()

    mark = texts.DISCOUNT_ADMIN_APPROVED if callback_data.approve else texts.DISCOUNT_ADMIN_REJECTED
    if call.message is not None:
        try:
            await call.message.edit_text(call.message.html_text + mark, reply_markup=None)
        except Exception:
            logger.debug("Не удалось отметить заявку на скидку", exc_info=True)

    title = _title(request.discount)
    text = (
        texts.DISCOUNT_APPROVED.format(title=title)
        if callback_data.approve
        else texts.DISCOUNT_REJECTED.format(title=title)
    )
    try:
        await call.bot.send_message(request.user_id, text, reply_markup=menu_keyboard())
    except Exception:
        logger.warning("Не удалось сообщить %s о решении по скидке", request.user_id, exc_info=True)
