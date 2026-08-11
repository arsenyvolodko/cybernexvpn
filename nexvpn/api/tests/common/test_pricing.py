"""Арифметика тарифов. Чистые функции — БД не нужна."""

import pytest

from nexvpn.subscription import pricing

PRICE_1 = 150
PRICE_3 = 400
PRICE_10 = 1000


def test_days_from_amount_rounds_down():
    # 400₽ на тарифе 400₽/30дн — ровно период.
    assert pricing.days_from_amount(400, PRICE_3) == 30
    # 399₽ не дотягивает до полного периода.
    assert pricing.days_from_amount(399, PRICE_3) == 29


def test_days_from_amount_generous_rounds_up():
    assert pricing.days_from_amount_generous(300, PRICE_3) == 23  # 22.5 → 23
    assert pricing.days_from_amount(300, PRICE_3) == 22


def test_upgrade_from_tz_example():
    """Кейс из ТЗ: 1 устройство, 30 дней остатка, переход на 3 устройства."""
    quote = pricing.quote_plan_change(days_left=30, price_from=PRICE_1, price_to=PRICE_3)

    assert quote.is_upgrade
    assert quote.topup_price == 250  # 400 − 150
    assert quote.topup_days == 30
    # Без доплаты остаток пересчитывается по новой цене: 150₽ хватает на 11 дней.
    assert quote.converted_days == 11


def test_upgrade_with_large_remainder_does_not_burn_money():
    """После миграции у людей будут сотни дней — наивная формула их бы съела."""
    quote = pricing.quote_plan_change(days_left=300, price_from=PRICE_1, price_to=PRICE_3)

    # 300 дней по 150₽ = 1500₽; на тарифе 400₽ это 112 дней.
    assert quote.converted_days == 112
    # Доплата не предлагается: «заплати 0₽ и получи 30 дней вместо 112» — это отъём.
    assert quote.topup_price is None
    assert not quote.can_topup


def test_conversion_preserves_value_both_directions():
    days_left, price_from, price_to = 60, PRICE_10, PRICE_3
    converted = pricing.convert_days_between_plans(days_left, price_from, price_to)

    value_before = pricing.value_of_days(days_left, price_from)
    value_after = pricing.value_of_days(converted, price_to)
    # Потери только на округлении вниз, и не больше стоимости одного дня.
    assert 0 <= value_before - value_after < price_to / pricing.days_in_period() + 1


def test_topup_price_never_negative():
    assert pricing.topup_price_for_full_period(days_left=999, price_from=PRICE_10, price_to=PRICE_1) == 0


def test_no_days_left_means_full_price():
    quote = pricing.quote_plan_change(days_left=0, price_from=PRICE_1, price_to=PRICE_3)
    assert quote.converted_days == 0
    assert quote.topup_price == PRICE_3


@pytest.mark.parametrize(
    "devices,expected",
    [(1, 1), (2, 3), (3, 3), (4, 5), (5, 5), (6, 7), (7, 7), (8, 10), (10, 10), (25, 10)],
)
def test_plan_for_device_count(devices, expected):
    assert pricing.plan_for_device_count(devices, [1, 3, 5, 7, 10]) == expected
