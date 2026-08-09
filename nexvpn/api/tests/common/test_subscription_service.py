"""Поведение подписки: гранты, смена тарифа, рефералка."""

from datetime import timedelta

import pytest
from django.utils.timezone import now

from nexvpn.enums import SubscriptionEventReasonEnum
from nexvpn.models import Plan, Subscription, SubscriptionEvent, UserInvitation
from nexvpn.api.tests.factories import NexUserFactory, PlanFactory, SubscriptionFactory
from nexvpn.subscription import service

pytestmark = pytest.mark.django_db


@pytest.fixture
def plans():
    return {
        limit: PlanFactory(device_limit=limit, price_month=price)
        for limit, price in [(1, 150), (3, 400), (5, 600), (7, 750), (10, 1000)]
    }


def test_trial_for_new_user(plans):
    user = NexUserFactory(id=1, is_legacy=False)

    subscription = service.grant_trial(user)

    assert subscription is not None
    assert subscription.plan.device_limit == 3
    assert subscription.days_left == 3
    assert SubscriptionEvent.objects.get(user=user).reason == SubscriptionEventReasonEnum.TRIAL


def test_no_trial_for_legacy_user(plans):
    """Тот, кто просто зашёл в обновлённого бота, — не новый пользователь."""
    user = NexUserFactory(id=2, is_legacy=True)

    assert service.grant_trial(user) is None
    assert not Subscription.objects.filter(user=user).exists()


def test_trial_is_not_granted_twice(plans):
    user = NexUserFactory(id=3, is_legacy=False)

    service.grant_trial(user)
    assert service.grant_trial(user) is None
    assert SubscriptionEvent.objects.filter(user=user).count() == 1


def test_grant_days_extends_active_subscription(plans):
    subscription = SubscriptionFactory(plan=plans[3], expires_at=now() + timedelta(days=10))

    service.grant_days(subscription.user, 5, SubscriptionEventReasonEnum.ADMIN_ADJUSTMENT)

    subscription.refresh_from_db()
    assert subscription.days_left == 15


def test_grant_days_restarts_expired_subscription(plans):
    """Сгоревшее время не возвращается: отсчёт от сегодня, а не от старой даты."""
    subscription = SubscriptionFactory(plan=plans[3], expires_at=now() - timedelta(days=40))

    service.grant_days(subscription.user, 10, SubscriptionEventReasonEnum.ADMIN_ADJUSTMENT)

    subscription.refresh_from_db()
    assert subscription.days_left == 10


def test_upgrade_converts_remainder(plans):
    subscription = SubscriptionFactory(plan=plans[1], expires_at=now() + timedelta(days=30))

    service.change_plan_now(subscription.user, plans[3])

    subscription.refresh_from_db()
    assert subscription.plan == plans[3]
    assert subscription.days_left == 11  # 30 дн. × 150₽ / 400₽


def test_upgrade_with_topup_gives_full_period(plans):
    subscription = SubscriptionFactory(plan=plans[1], expires_at=now() + timedelta(days=30))

    service.change_plan_now(subscription.user, plans[3], amount_paid=250)

    subscription.refresh_from_db()
    assert subscription.days_left == 30


def test_upgrade_rejects_underpayment(plans):
    subscription = SubscriptionFactory(plan=plans[1], expires_at=now() + timedelta(days=30))

    with pytest.raises(service.SubscriptionError):
        service.change_plan_now(subscription.user, plans[3], amount_paid=100)


def test_downgrade_is_deferred_to_next_period(plans):
    """До конца оплаченного периода у человека остаются все его устройства."""
    subscription = SubscriptionFactory(plan=plans[5], expires_at=now() + timedelta(days=20))
    expires_before = subscription.expires_at

    service.schedule_plan_downgrade(subscription.user, plans[1])

    subscription.refresh_from_db()
    assert subscription.plan == plans[5]
    assert subscription.next_plan == plans[1]
    assert subscription.expires_at == expires_before

    service.apply_scheduled_downgrade(subscription)
    subscription.refresh_from_db()
    assert subscription.plan == plans[1]
    assert subscription.next_plan is None


def test_downgrade_applies_lazily_without_a_scheduler(plans):
    """Планировщик для этого не нужен: применяем при первом обращении после срока."""
    subscription = SubscriptionFactory(plan=plans[5], expires_at=now() + timedelta(days=20))
    service.schedule_plan_downgrade(subscription.user, plans[1])

    # Пока период идёт — тариф прежний, все устройства при человеке.
    service.ensure_current_plan(subscription)
    subscription.refresh_from_db()
    assert subscription.plan == plans[5]

    Subscription.objects.filter(pk=subscription.pk).update(expires_at=now() - timedelta(days=1))
    subscription.refresh_from_db()
    service.ensure_current_plan(subscription)

    subscription.refresh_from_db()
    assert subscription.plan == plans[1]
    assert subscription.next_plan is None


def test_purchase_extends_and_clears_scheduled_downgrade(plans):
    subscription = SubscriptionFactory(plan=plans[3], expires_at=now() - timedelta(days=1))
    subscription.next_plan = plans[1]
    subscription.save()

    service.purchase_period(subscription.user, plans[1], amount=150)

    subscription.refresh_from_db()
    assert subscription.plan == plans[1]
    assert subscription.next_plan is None
    assert subscription.days_left == 30


def test_referral_rewards_only_after_first_payment(plans):
    inviter = NexUserFactory(id=10)
    invitee = NexUserFactory(id=11)
    SubscriptionFactory(user=inviter, plan=plans[3], expires_at=now() + timedelta(days=10))
    SubscriptionFactory(user=invitee, plan=plans[3], expires_at=now() + timedelta(days=3))

    service.register_invitation(inviter, invitee)

    inviter.subscription.refresh_from_db()
    assert inviter.subscription.days_left == 10  # пока ничего не начислено

    service.purchase_period(invitee, plans[3], amount=400)

    inviter.subscription.refresh_from_db()
    invitee.subscription.refresh_from_db()
    assert inviter.subscription.days_left == 20  # 10 + бонус 10
    assert invitee.subscription.days_left == 43  # 3 + 30 оплаченных + 10 бонусных
    assert UserInvitation.objects.get(invitee=invitee).reward_granted_at is not None


def test_referral_rewards_granted_once(plans):
    inviter = NexUserFactory(id=12)
    invitee = NexUserFactory(id=13)
    SubscriptionFactory(user=inviter, plan=plans[3], expires_at=now() + timedelta(days=10))
    SubscriptionFactory(user=invitee, plan=plans[3], expires_at=now() + timedelta(days=3))
    service.register_invitation(inviter, invitee)

    service.purchase_period(invitee, plans[3], amount=400)
    service.purchase_period(invitee, plans[3], amount=400)

    inviter.subscription.refresh_from_db()
    assert inviter.subscription.days_left == 20  # второй раз бонус не пришёл


def test_self_referral_rejected(plans):
    user = NexUserFactory(id=14)
    with pytest.raises(service.SubscriptionError):
        service.register_invitation(user, user)


def test_legacy_user_cannot_be_invitee(plans):
    """Иначе старую базу распилят по реферальным ссылкам."""
    inviter = NexUserFactory(id=15)
    invitee = NexUserFactory(id=16, is_legacy=True)

    with pytest.raises(service.SubscriptionError):
        service.register_invitation(inviter, invitee)


def test_plan_prices_are_editable_without_touching_history(plans):
    """Смена цены в админке не переписывает уже начисленное."""
    subscription = SubscriptionFactory(plan=plans[3], expires_at=now() + timedelta(days=5))
    service.purchase_period(subscription.user, plans[3], amount=400)

    Plan.objects.filter(pk=plans[3].pk).update(price_month=500)

    event = SubscriptionEvent.objects.filter(reason=SubscriptionEventReasonEnum.PURCHASE).get()
    assert event.price_month == 400
    assert event.amount == 400
