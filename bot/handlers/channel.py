"""Кнопка «Я подписался».

Экран подписки показывается до приветствия, поэтому здесь же собирается и само
приветствие — иначе человек, прошедший проверку, увидел бы пустое место вместо
рассказа о том, куда попал.
"""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from django.conf import settings

from bot import texts
from bot.channel import CHECK_CALLBACK, is_subscribed
from bot.handlers.common import render
from bot.keyboards import keyboards
from bot.services import get_trial_plan, mark_joined_channel, was_invited
from nexvpn.models import NexUser

logger = logging.getLogger(__name__)

router = Router(name="channel")


async def welcome_text(user: NexUser) -> str:
    plan = await get_trial_plan()
    text = texts.WELCOME.format(
        trial=texts.plural_days(settings.TRIAL_DAYS),
        plan=plan.name if plan else texts.plural_devices(settings.TRIAL_PLAN_DEVICES),
    )
    if await was_invited(user):
        text += texts.WELCOME_REFERRAL
    return text


@router.callback_query(F.data == CHECK_CALLBACK)
async def handle_check(call: CallbackQuery, user: NexUser) -> None:
    if not await is_subscribed(call.bot, user.pk):
        # Алертом, а не новым экраном: человек никуда не ушёл, ему нужен лишь
        # намёк, что мы его пока не видим.
        await call.answer(texts.CHANNEL_GATE_NOT_YET, show_alert=True)
        return

    await call.answer()
    await mark_joined_channel(user)
    await render(call, await welcome_text(user), keyboards.welcome())
