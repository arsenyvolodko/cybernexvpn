"""Конвертация старой базы. Чистая функция — БД не нужна."""

from datetime import date

import pytest
from django.test import override_settings

from nexvpn.subscription import legacy

PRICES = {1: 150, 3: 400, 5: 600, 7: 750, 10: 1000}
TODAY = date(2026, 8, 9)


def make(balance=0, devices=0, end_date=None):
    return legacy.LegacyUser(
        user_id=1, username="u", balance=balance, device_count=devices, max_end_date=end_date
    )


def convert(**kwargs):
    return legacy.convert(make(**kwargs), PRICES, TODAY)


@pytest.mark.parametrize(
    "devices,expected_plan", [(1, 1), (2, 3), (3, 3), (4, 5), (5, 5), (6, 7), (7, 7), (8, 10), (10, 10)]
)
def test_plan_follows_device_count(devices, expected_plan):
    assert convert(devices=devices).plan_device_limit == expected_plan


def test_more_than_ten_devices_is_trimmed():
    result = convert(devices=12)
    assert result.plan_device_limit == 10
    assert result.devices_trimmed == 2


def test_no_devices_gets_gift_month():
    """Эти уже ушли — 30 дней на тарифе 3 устройства как попытка вернуть."""
    result = convert(devices=0)
    assert result.plan_device_limit == 3
    assert result.days_granted == 30


def test_balance_converts_at_new_plan_price_rounded_up():
    result = convert(balance=300, devices=2)
    assert result.plan_device_limit == 3
    assert result.days_from_balance == 23  # 300 × 30 / 400 = 22.5 → 23


def test_remaining_paid_period_is_added():
    result = convert(balance=0, devices=1, end_date=date(2026, 9, 1))
    assert result.remaining_paid_days == 23
    assert result.days_granted == 23


def test_floor_protects_users_whose_period_ran_out_during_the_outage():
    """С 3 августа бот не работал — пополниться было негде, без порога был бы 0."""
    result = convert(balance=0, devices=1, end_date=date(2026, 8, 5))
    assert result.days_total == 0
    assert result.days_granted == 7
    assert result.floored


def test_floor_does_not_touch_normal_grants():
    result = convert(balance=0, devices=1, end_date=date(2026, 9, 1))
    assert not result.floored


@override_settings(LEGACY_UNLIMITED_BALANCE=1000, LEGACY_MAX_DAYS=180)
def test_hand_filled_balance_becomes_unlimited_on_top_plan():
    """99 000₽ никто не платил — это подарок, конвертировать его в дни бессмысленно."""
    result = convert(balance=99400, devices=2)

    assert result.unlimited
    assert result.plan_device_limit == 10
    assert result.days_granted == 3650
    assert not result.capped


@override_settings(LEGACY_UNLIMITED_BALANCE=1000, LEGACY_MAX_DAYS=180)
def test_balance_below_threshold_is_converted_and_capped():
    result = convert(balance=1000, devices=1)

    assert not result.unlimited
    assert result.days_total == 200  # 1000 × 30 / 150
    assert result.days_granted == 180
    assert result.capped
