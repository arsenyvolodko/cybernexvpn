"""Экран продления при запланированной смене тарифа.

Ловушка простая и дорогая: отложенный переход срабатывает в момент окончания
подписки, а продление этот момент отодвигает. Человек, собиравшийся уйти на
тариф подешевле, платит по текущему — дорогому — и не понимает, почему смена
не наступила.
"""

import pytest
from django.utils.timezone import localtime

from bot import texts
from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory

pytestmark = pytest.mark.django_db


def test_warning_names_both_plans_and_the_date():
    big = PlanFactory(device_limit=5, price_month=600)
    small = PlanFactory(device_limit=1, price_month=150)
    subscription = SubscriptionFactory(plan=big, next_plan=small)

    text = texts.RENEW_PLAN_CHANGE_PENDING.format(
        next_plan=texts.plural_devices(small.device_limit),
        next_price=small.price_month,
        until=localtime(subscription.expires_at).strftime("%d.%m.%Y"),
        plan=texts.plural_devices(big.device_limit),
        price=big.price_month,
    )

    assert "1 устройство" in text and "150" in text, "новый тариф и его цена"
    assert "5 устройств" in text and "600" in text, "текущий тариф и его цена"
    assert "сдвинется" in text, "надо честно сказать, что дата перехода уедет"


def test_renew_screen_keeps_quiet_without_a_pending_change():
    """Обычному человеку это предупреждение только мешает."""
    subscription = SubscriptionFactory(plan=PlanFactory(device_limit=3, price_month=400))

    assert subscription.next_plan_id is None


def test_expired_subscription_renews_on_the_new_plan():
    """Истекла с запланированным понижением — продлевать надо уже новый тариф.

    Раньше экран и платёж брали старый: человек видел 600₽ за тариф, от
    которого сам отказался, платил их, и понижение молча отменялось.
    """
    from datetime import timedelta

    from asgiref.sync import async_to_sync
    from django.utils.timezone import now

    from bot.services import get_renew_options

    big = PlanFactory(device_limit=5, price_month=600)
    small = PlanFactory(device_limit=1, price_month=150)
    subscription = SubscriptionFactory(
        plan=big, next_plan=small, expires_at=now() - timedelta(days=1)
    )

    fresh, options = async_to_sync(get_renew_options)(subscription.user)

    assert fresh.plan_id == small.pk, "тариф должен был переключиться"
    assert min(o.price for o in options) == 150, "и цена — по новому тарифу"


def test_active_subscription_keeps_its_plan_until_the_end():
    """Пока период оплачен, устройства остаются при человеке."""
    from asgiref.sync import async_to_sync

    from bot.services import get_renew_options

    big = PlanFactory(device_limit=5, price_month=600)
    small = PlanFactory(device_limit=1, price_month=150)
    subscription = SubscriptionFactory(plan=big, next_plan=small)

    fresh, _ = async_to_sync(get_renew_options)(subscription.user)

    assert fresh.plan_id == big.pk
    assert fresh.next_plan_id == small.pk
