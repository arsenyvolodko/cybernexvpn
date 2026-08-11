import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot import texts
from bot.handlers.common import render
from bot.keyboards import ButtonsStorage, keyboards
from bot.services import SubscriptionView, get_subscription_view
from nexvpn.models import NexUser

logger = logging.getLogger(__name__)

router = Router(name="subscription")


def build_text(view: SubscriptionView) -> str:
    if not view.exists:
        return texts.SUBSCRIPTION_NONE

    subscription = view.subscription
    plan_title = texts.plural_devices(subscription.plan.device_limit)
    until = subscription.expires_at.strftime("%d.%m.%Y")

    if not view.is_active:
        return texts.SUBSCRIPTION_EXPIRED.format(devices=plan_title, until=until)

    used = view.devices_used
    text = texts.SUBSCRIPTION_ACTIVE.format(
        devices=plan_title,
        until=until,
        days_left=texts.plural_days(subscription.days_left),
        used="?" if used is None else used,
        limit=view.device_limit,
    )
    if used is None:
        text += texts.DEVICES_UNAVAILABLE
    if subscription.next_plan_id is not None:
        text += texts.SUBSCRIPTION_PLAN_CHANGE_SCHEDULED.format(
            date=until, plan=texts.plural_devices(subscription.next_plan.device_limit)
        )
    return text


@router.callback_query(F.data == ButtonsStorage.MY_SUBSCRIPTION.callback)
async def handle_my_subscription(call: CallbackQuery, user: NexUser) -> None:
    await call.answer()
    view = await get_subscription_view(user)
    await render(
        call,
        build_text(view),
        keyboards.subscription(
            is_active=view.is_active,
            web_url=view.web_url,
            can_add_device=view.can_add_device,
        ),
    )
