"""Скидки за срок и покупка на несколько месяцев."""

import pytest

from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory
from nexvpn.enums import SubscriptionEventReasonEnum
from nexvpn.models import BillingPeriod, SubscriptionEvent
from nexvpn.subscription import pricing, service

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "months,discount,expected",
    [
        (1, 0, 400),
        (3, 10, 1080),
        (6, 15, 2040),
        (12, 25, 3600),
    ],
)
def test_period_price(months, discount, expected):
    assert pricing.period_price(400, months, discount) == expected


def test_discount_rounds_in_the_users_favour():
    """333₽ × 3 × 0.9 = 899.1 — платит 899, а не 900."""
    assert pricing.period_price(333, 3, 10) == 899


def test_saving_is_shown_against_the_monthly_price():
    """Сроки заводит миграция, поэтому берём готовый, а не создаём второй такой же."""
    plan = PlanFactory(device_limit=3, price_month=400)
    period = BillingPeriod.objects.get(months=12)
    period.discount_percent = 25
    period.save()

    assert period.full_price_for(plan) == 4800
    assert period.price_for(plan) == 3600
    assert period.saving_for(plan) == 1200
    assert period.days == 360


def test_buying_several_months_grants_all_of_them():
    plan = PlanFactory(device_limit=3, price_month=400)
    subscription = SubscriptionFactory(plan=plan)
    before = subscription.expires_at

    service.purchase_period(subscription.user, plan, amount=1080, months=3)

    subscription.refresh_from_db()
    granted = (subscription.expires_at - before).days
    # 90 дней плюс округление окончания до фиксированного часа.
    assert 90 <= granted <= 91


def test_event_records_the_full_amount():
    plan = PlanFactory(device_limit=3, price_month=400)
    subscription = SubscriptionFactory(plan=plan)

    service.purchase_period(subscription.user, plan, amount=1080, months=3)

    event = SubscriptionEvent.objects.get(reason=SubscriptionEventReasonEnum.PURCHASE)
    assert event.amount == 1080
    assert event.delta_days == 90
    assert "3 мес." in event.comment


def test_single_month_stays_the_default():
    plan = PlanFactory(device_limit=3, price_month=400)
    subscription = SubscriptionFactory(plan=plan)

    service.purchase_period(subscription.user, plan, amount=400)

    assert SubscriptionEvent.objects.get(reason=SubscriptionEventReasonEnum.PURCHASE).delta_days == 30
