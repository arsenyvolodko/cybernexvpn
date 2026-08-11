import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from django.conf import settings

from bot import texts
from bot.handlers.common import render
from bot.keyboards import ButtonsStorage, keyboards
from bot.services import get_referral_view
from nexvpn.models import NexUser

logger = logging.getLogger(__name__)

router = Router(name="referral")

HISTORY_LIMIT = 10


@router.callback_query(F.data == ButtonsStorage.REFERRAL.callback)
async def handle_referral(call: CallbackQuery, user: NexUser) -> None:
    await call.answer()
    view = await get_referral_view(user)

    text = texts.REFERRAL.format(
        invitee_trial=texts.plural_days(settings.TRIAL_DAYS),
        inviter_days=texts.plural_days(settings.REFERRAL_INVITER_DAYS),
        invitee_days=texts.plural_days(settings.REFERRAL_INVITEE_DAYS),
        link=view.link,
        invited=view.invited,
        pending=view.pending,
        days_earned=texts.plural_days(view.days_earned),
    )

    if view.history:
        text += texts.REFERRAL_HISTORY_HEADER
        for date, days, _comment in view.history[:HISTORY_LIMIT]:
            text += texts.REFERRAL_HISTORY_ROW.format(date=date, days=texts.plural_days(days))
    else:
        text += texts.REFERRAL_NO_HISTORY

    await render(call, text, keyboards.referral(view.link))
