"""Напоминания об окончании подписки и нормализация времени окончания."""

from datetime import timedelta

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from django.utils.timezone import now

from bot.notifications import due_reminders
from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory
from nexvpn.enums import SubscriptionEventReasonEnum
from nexvpn.models import SentReminder, Subscription
from nexvpn.subscription import service

pytestmark = pytest.mark.django_db

OFFSETS = [168, 48, 24, 12, 6, 2, 1]


@pytest.fixture
def plan():
    return PlanFactory(device_limit=3, price_month=400)


def expiring_in(hours: float, plan) -> Subscription:
    return SubscriptionFactory(plan=plan, expires_at=now() + timedelta(hours=hours))


# --- нормализация времени окончания ---


def test_expiry_is_rounded_up_to_configured_hour(plan):
    subscription = SubscriptionFactory(plan=plan, expires_at=now() - timedelta(days=1))

    service.grant_days(subscription.user, 30, SubscriptionEventReasonEnum.ADMIN_ADJUSTMENT)

    subscription.refresh_from_db()
    local = timezone.localtime(subscription.expires_at)
    assert (local.hour, local.minute, local.second) == (settings.SUBSCRIPTION_EXPIRY_HOUR, 0, 0)


def test_rounding_never_takes_time_away(plan):
    """Округляем только вверх: человек может получить часы сверху, но не потерять."""
    subscription = SubscriptionFactory(plan=plan, expires_at=now() - timedelta(days=1))
    before = now()

    service.grant_days(subscription.user, 7, SubscriptionEventReasonEnum.ADMIN_ADJUSTMENT)

    subscription.refresh_from_db()
    assert subscription.expires_at >= before + timedelta(days=7)


@override_settings(SUBSCRIPTION_EXPIRY_HOUR=20, SUBSCRIPTION_REMINDER_HOURS=OFFSETS)
def test_no_reminder_lands_at_night():
    """Смысл фиксированного часа: ни одно напоминание не приходит ночью."""
    expiry = timezone.localtime(now()).replace(hour=20, minute=0, second=0, microsecond=0)
    for offset in OFFSETS:
        hour = (expiry - timedelta(hours=offset)).hour
        assert 8 <= hour <= 20, f"напоминание за {offset} ч. попадает на {hour}:00"


# --- выбор, кому пора напомнить ---


@override_settings(SUBSCRIPTION_REMINDER_HOURS=OFFSETS)
def test_picks_the_closest_offset(plan):
    """Если задача простояла, шлём «остался час», а не пачку из четырёх сообщений."""
    subscription = expiring_in(0.5, plan)

    due = due_reminders()

    assert due == [(subscription, 1)] or (len(due) == 1 and due[0][1] == 1)


@override_settings(SUBSCRIPTION_REMINDER_HOURS=OFFSETS)
def test_reminder_is_sent_once(plan):
    subscription = expiring_in(5, plan)

    assert due_reminders()[0][1] == 6
    SentReminder.objects.create(
        subscription=subscription, hours_before=6, expires_at=subscription.expires_at
    )

    assert due_reminders() == []


@override_settings(SUBSCRIPTION_REMINDER_HOURS=OFFSETS)
def test_next_offset_fires_later(plan):
    subscription = expiring_in(5, plan)
    SentReminder.objects.create(
        subscription=subscription, hours_before=6, expires_at=subscription.expires_at
    )

    # Время подошло ближе — должно сработать следующее смещение.
    Subscription.objects.filter(pk=subscription.pk).update(expires_at=now() + timedelta(hours=1.5))

    due = due_reminders()
    assert len(due) == 1 and due[0][1] == 2


@override_settings(SUBSCRIPTION_REMINDER_HOURS=OFFSETS)
def test_expired_subscription_is_not_reminded(plan):
    expiring_in(-1, plan)
    assert due_reminders() == []


@override_settings(SUBSCRIPTION_REMINDER_HOURS=OFFSETS)
def test_far_subscription_is_not_reminded(plan):
    expiring_in(24 * 20, plan)
    assert due_reminders() == []


@override_settings(SUBSCRIPTION_REMINDER_HOURS=OFFSETS)
def test_renewal_resets_reminders(plan):
    """Начался новый период — напоминать по нему надо заново."""
    subscription = expiring_in(1, plan)
    SentReminder.objects.create(
        subscription=subscription, hours_before=1, expires_at=subscription.expires_at
    )

    service.grant_days(subscription.user, 30, SubscriptionEventReasonEnum.PURCHASE)

    assert not SentReminder.objects.filter(subscription=subscription).exists()
