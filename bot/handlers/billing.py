"""Продление подписки и смена тарифа.

Обе ветки ведут в одну и ту же оплату, но по-разному считают сумму, поэтому
живут рядом. Все расчёты берутся из `nexvpn.subscription`: бот только
показывает то, что посчитал биллинг, и ничего не считает сам — иначе цифры в
боте и в чеке однажды разойдутся.
"""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from django.conf import settings
from django.utils.timezone import localtime

from bot import texts
from bot.handlers.common import render
from bot.keyboards import ButtonsStorage, keyboards
from bot.keyboards.factories import PlanCallback, RenewCallback
from bot.services import (
    change_plan_free,
    get_plan_options,
    get_renew_options,
    receipt_needs_email,
    remember_payment_screen,
    set_email,
    start_plan_change_payment,
    start_renew_payment,
)
from nexvpn.models import NexUser
from nexvpn.subscription.service import SubscriptionError

logger = logging.getLogger(__name__)

router = Router(name="billing")


class EmailForm(StatesGroup):
    waiting = State()


async def _needs_email(call: CallbackQuery, user: NexUser, state: FSMContext, resume: str) -> bool:
    """Спросить email, если без него не выписать чек. `resume` — куда вернуться."""
    if not await receipt_needs_email(user):
        return False
    await state.set_state(EmailForm.waiting)
    await state.update_data(resume=resume)
    await render(call, texts.ASK_EMAIL, keyboards.only_back())
    return True


# --- продление ---


@router.callback_query(F.data == ButtonsStorage.RENEW.callback)
async def handle_renew(call: CallbackQuery, user: NexUser) -> None:
    await call.answer()
    subscription, options = await get_renew_options(user)
    if subscription is None or not options:
        await render(call, texts.SUBSCRIPTION_NONE, keyboards.only_back())
        return

    text = texts.RENEW.format(
        plan=texts.plural_devices(subscription.plan.device_limit),
        price=subscription.plan.price_month,
    )
    if subscription.next_plan_id:
        text += texts.RENEW_PLAN_CHANGE_PENDING.format(
            next_plan=texts.plural_devices(subscription.next_plan.device_limit),
            next_price=subscription.next_plan.price_month,
            until=localtime(subscription.expires_at).strftime("%d.%m.%Y"),
            plan=texts.plural_devices(subscription.plan.device_limit),
            price=subscription.plan.price_month,
        )

    await render(call, text, keyboards.renew(options))


@router.callback_query(RenewCallback.filter())
async def handle_renew_period(
    call: CallbackQuery, callback_data: RenewCallback, user: NexUser, state: FSMContext
) -> None:
    await call.answer()
    if await _needs_email(call, user, state, f"renew:{callback_data.months}"):
        return
    await _start_renew(call, user, callback_data.months)


async def _start_renew(event: CallbackQuery | Message, user: NexUser, months: int) -> None:
    """`event` может быть и нажатием, и сообщением с почтой — render разберётся."""
    try:
        url, payment_id = await start_renew_payment(user, months, settings.TG_BOT_URL)
    except Exception:
        logger.exception("Не удалось создать платёж за продление")
        await render(event, texts.PAYMENT_FAILED, keyboards.only_back())
        return
    message_id = await render(event, texts.PAYMENT_READY, keyboards.pay(url))
    # Запоминаем экран, чтобы вебхук поправил именно его, когда деньги дойдут.
    await remember_payment_screen(payment_id, message_id)


# --- смена тарифа ---


@router.callback_query(F.data == ButtonsStorage.CHANGE_PLAN.callback)
async def handle_change_plan(call: CallbackQuery, user: NexUser) -> None:
    await call.answer()
    subscription, options = await get_plan_options(user)
    if subscription is None:
        await render(call, texts.SUBSCRIPTION_NONE, keyboards.only_back())
        return

    await render(
        call,
        texts.CHANGE_PLAN.format(
            plan=texts.plural_devices(subscription.plan.device_limit),
            days_left=texts.plural_days(subscription.days_left),
        ),
        keyboards.plan_list(options),
    )


@router.callback_query(PlanCallback.filter(F.action == "open"))
async def handle_plan_details(call: CallbackQuery, callback_data: PlanCallback, user: NexUser) -> None:
    await call.answer()
    subscription, options = await get_plan_options(user)
    option = next((o for o in options if o.device_limit == callback_data.device_limit), None)
    if subscription is None or option is None:
        await render(call, texts.SOMETHING_WENT_WRONG, keyboards.only_back())
        return

    if option.is_upgrade:
        text = texts.PLAN_UPGRADE.format(
            plan=texts.plural_devices(option.device_limit),
            price=option.price_month,
            days_left=texts.plural_days(subscription.days_left),
            converted=texts.plural_days(option.converted_days),
        )
        if option.topup_price:
            text += texts.PLAN_UPGRADE_TOPUP.format(
                price=option.topup_price, days=texts.plural_days(30)
            )
    else:
        text = texts.PLAN_DOWNGRADE.format(
            plan=texts.plural_devices(option.device_limit),
            price=option.price_month,
            until=subscription.expires_at.strftime("%d.%m.%Y"),
            current=texts.plural_devices(subscription.plan.device_limit),
        )

    await render(call, text, keyboards.plan_change(option))


@router.callback_query(PlanCallback.filter(F.action == "free"))
async def handle_plan_free(call: CallbackQuery, callback_data: PlanCallback, user: NexUser) -> None:
    try:
        subscription = await change_plan_free(user, callback_data.device_limit)
    except SubscriptionError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await call.answer()
    if subscription.next_plan_id is not None:
        text = texts.PLAN_DOWNGRADE_SCHEDULED.format(
            plan=texts.plural_devices(subscription.next_plan.device_limit),
            date=subscription.expires_at.strftime("%d.%m.%Y"),
        )
    else:
        text = texts.PLAN_CHANGED.format(
            plan=texts.plural_devices(subscription.plan.device_limit),
            days_left=texts.plural_days(subscription.days_left),
        )
    await render(call, text, keyboards.only_back())


@router.callback_query(PlanCallback.filter(F.action == "pay"))
async def handle_plan_pay(
    call: CallbackQuery, callback_data: PlanCallback, user: NexUser, state: FSMContext
) -> None:
    await call.answer()
    if await _needs_email(call, user, state, f"plan:{callback_data.device_limit}"):
        return
    await _start_plan_payment(call, user, callback_data.device_limit)


async def _start_plan_payment(event: CallbackQuery | Message, user: NexUser, device_limit: int) -> None:
    try:
        url = await start_plan_change_payment(user, device_limit, settings.TG_BOT_URL)
    except SubscriptionError as exc:
        await render(event, str(exc), keyboards.only_back())
        return
    except Exception:
        logger.exception("Не удалось создать платёж за смену тарифа")
        await render(event, texts.PAYMENT_FAILED, keyboards.only_back())
        return
    await render(event, texts.PAYMENT_READY, keyboards.pay(url))


# --- email для чека ---


@router.message(EmailForm.waiting)
async def handle_email(message: Message, user: NexUser, state: FSMContext) -> None:
    email = (message.text or "").strip()
    if "@" not in email or " " in email or len(email) < 5:
        await message.answer(texts.EMAIL_INVALID)
        return

    data = await state.get_data()
    await state.clear()
    await set_email(user, email)

    await message.answer(texts.EMAIL_SAVED.format(email=email))

    # Продолжаем прерванный сценарий, чтобы человек не искал кнопку заново.
    resume = data.get("resume", "")
    if resume.startswith("renew:"):
        await _start_renew(message, user, int(resume.split(":")[1]))
    elif resume.startswith("plan:"):
        await _start_plan_payment(message, user, int(resume.split(":")[1]))
