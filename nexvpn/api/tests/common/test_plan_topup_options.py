"""Сроки доплаты при смене тарифа и минимальная цена месяца в списке тарифов.

Экран «доплати и получи месяц» превратился в выбор срока: 1/3/6/12 месяцев с
той же скидкой, что и у продления. Список тарифов при этом честно показывает
не цену месяца в лоб, а минимальную — по самому длинному сроку, «от».
Оба расчёта опираются на один и тот же засеянный `BillingPeriod`
(миграция `0028_billing_periods`: 1/3/6/12 мес., скидки 0/10/15/25%).
"""

import datetime as dt

import pytest
from asgiref.sync import async_to_sync
from django.utils.timezone import now

from bot.services import get_plan_options, get_plan_topup_options
from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory

pytestmark = pytest.mark.django_db


def test_topup_options_cover_every_active_period():
    cheap = PlanFactory(device_limit=1, price_month=100)
    pricey = PlanFactory(device_limit=10, price_month=5000)
    subscription = SubscriptionFactory(plan=cheap, expires_at=now() + dt.timedelta(hours=20))

    options = async_to_sync(get_plan_topup_options)(subscription.user, pricey.device_limit)

    assert sorted(o.months for o in options) == [1, 3, 6, 12]
    assert all(o.price > 0 for o in options)


def test_longer_period_is_cheaper_per_month():
    """Смысл выбора срока: скидка должна быть реально заметна."""
    cheap = PlanFactory(device_limit=1, price_month=100)
    pricey = PlanFactory(device_limit=10, price_month=5000)
    subscription = SubscriptionFactory(plan=cheap, expires_at=now() + dt.timedelta(hours=20))

    options = async_to_sync(get_plan_topup_options)(subscription.user, pricey.device_limit)
    by_months = {o.months: o.price for o in options}

    assert by_months[12] / 12 < by_months[1]


def test_topup_options_credit_the_remaining_days_once():
    """Кредит от остатка старого тарифа вычитается один раз, а не на каждый месяц."""
    cheap = PlanFactory(device_limit=1, price_month=150)
    pricey = PlanFactory(device_limit=3, price_month=400)
    subscription = SubscriptionFactory(plan=cheap, expires_at=now() + dt.timedelta(days=5))

    options = async_to_sync(get_plan_topup_options)(subscription.user, pricey.device_limit)
    by_months = {o.months: o.price for o in options}

    credit = 5 * 150 // 30
    assert by_months[1] == 400 - credit
    assert by_months[3] == (400 * 3 * 90 // 100) - credit  # скидка 10% за 3 месяца


def test_min_price_month_uses_the_longest_period():
    plan = PlanFactory(device_limit=3, price_month=400)
    other = PlanFactory(device_limit=5, price_month=600)
    subscription = SubscriptionFactory(plan=plan, expires_at=now() + dt.timedelta(days=30))

    _, options = async_to_sync(get_plan_options)(subscription.user)
    option = next(o for o in options if o.device_limit == other.device_limit)

    # 12 мес. × 600₽ × 0.75 // 12 = 450₽ — дешевле полной цены месяца.
    assert option.min_price_month == 450
    assert option.min_price_month < option.price_month
