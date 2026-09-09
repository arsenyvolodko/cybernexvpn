"""Короткий сценарий для истёкшей подписки.

Общий сценарий смены тарифа рассчитан на живую подписку: он считает остаток
дней, показывает экран подтверждения и спрашивает, переходить бесплатно или
с доплатой. У истёкшей подписки остатка нет — пересчитывать нечего, доплачивать
не за что, и все эти шаги превращаются в лишние нажатия между человеком и
оплатой.

Поэтому здесь свой набор экранов, всего четыре:

    сообщение об окончании
      ├─ Продлить подписку → сроки → оплата
      │                       └ назад к сообщению
      └─ Сменить тариф → список тарифов → тариф применён сразу
                          └ назад       └ Продлить → сроки → оплата

«Назад» ведёт к сообщению об окончании, а не в меню: человек пришёл сюда из
него, и возврат в меню выбросил бы его из сценария.
"""

import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot import texts
from bot.handlers.common import render
from bot.keyboards import keyboards
from bot.keyboards.factories import ExpiredCallback
from bot.services import change_plan_free, get_plan_options, get_renew_options
from nexvpn.models import NexUser
from nexvpn.subscription.service import SubscriptionError

logger = logging.getLogger(__name__)

router = Router(name="expired")


async def _renew_screen(call: CallbackQuery, user: NexUser, keyboard_for) -> None:
    """Сроки продления. Отличаются только наличием «Назад», поэтому общая."""
    subscription, options = await get_renew_options(user)
    if subscription is None or not options:
        await render(call, texts.SUBSCRIPTION_NONE, keyboards.only_back())
        return
    text = texts.RENEW.format(
        plan=texts.plural_devices(subscription.plan.device_limit),
        price=subscription.plan.price_month,
    )
    await render(call, text, keyboard_for(options))


@router.callback_query(ExpiredCallback.filter())
async def handle_expired_step(
    call: CallbackQuery, callback_data: ExpiredCallback, user: NexUser, state: FSMContext
) -> None:
    step = callback_data.step
    await call.answer()

    if step == "home":
        # Тот же текст, что пришёл человеку сообщением: возврат должен вести
        # ровно туда, откуда он ушёл, а не на похожий экран.
        await render(call, texts.SUBSCRIPTION_ENDED, keyboards.ended())
        return

    if step == "renew":
        await _renew_screen(call, user, keyboards.expired_renew)
        return

    if step == "renew_final":
        await _renew_screen(call, user, keyboards.expired_renew_final)
        return

    if step == "plans":
        subscription, options = await get_plan_options(user)
        if subscription is None:
            await render(call, texts.SUBSCRIPTION_NONE, keyboards.only_back())
            return
        await render(call, texts.CHANGE_PLAN_EXPIRED, keyboards.expired_plans(options))
        return

    if step == "pick":
        # Устройств может оказаться больше, чем в новом тарифе. Тогда сначала
        # разбираемся с ними: панель лишние сама не выбрасывает, и без этого
        # человек оказался бы в состоянии «Занято: 3 из 1».
        from bot.handlers.trim import show_warning

        if await show_warning(call, user, callback_data.device_limit):
            return
        try:
            await change_plan_free(user, callback_data.device_limit)
        except SubscriptionError as error:
            # Тариф мог стать неактивным, пока человек смотрел на список.
            await call.answer(str(error), show_alert=True)
            return
        await render(call, texts.PLAN_CHANGED_EXPIRED, keyboards.expired_changed())
        return

    logger.warning("Неизвестный шаг сценария истёкшей подписки: %s", step)
    await render(call, texts.SUBSCRIPTION_ENDED, keyboards.ended())
