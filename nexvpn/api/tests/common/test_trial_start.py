"""Когда начинается пробный период.

Раньше он выдавался в middleware при создании пользователя, то есть на первом
же `/start` — ещё до экрана подписки на канал. На 09.09.2026 из 36 выданных
пробных 14 не были использованы ни разу, у 12 из них дни успели сгореть.

Теперь отсчёт начинается по «Подключиться» — в тот момент, когда человеку и
так надо отдать ссылку на подписку. Привязать выдачу к первому подключению
устройства нельзя: без действующего срока панель не отдаст конфиги, и
подключиться, чтобы начать пробный, физически невозможно.
"""

import pytest
from asgiref.sync import async_to_sync

from bot import texts
from bot.handlers.connect import connect_screen
from bot.handlers.subscription import build_text
from bot.services import get_subscription_view, get_or_create_user
from nexvpn.api.tests.factories import NexUserFactory, PlanFactory, SubscriptionFactory
from nexvpn.enums import SubscriptionEventReasonEnum
from nexvpn.models import Subscription, SubscriptionEvent
from nexvpn.subscription import service

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def trial_plan(settings):
    settings.TRIAL_DAYS = 3
    settings.TRIAL_PLAN_DEVICES = 3
    return PlanFactory(device_limit=3, price_month=400)


@pytest.fixture(autouse=True)
def no_panel(monkeypatch):
    """Панель в тестах не трогаем — синхронизация подписки не проверяется здесь."""
    monkeypatch.setattr("bot.services._sync_quietly", lambda subscription: None)
    monkeypatch.setattr("bot.services.panel_sync.list_devices", lambda subscription: [])


def test_start_no_longer_starts_the_countdown():
    """Главное: первый заход в бота больше не жжёт пробные дни."""
    user, created = async_to_sync(get_or_create_user)(555, "vasya", "Вася")

    assert created
    assert not Subscription.objects.filter(user=user).exists()
    assert not SubscriptionEvent.objects.filter(reason=SubscriptionEventReasonEnum.TRIAL).exists()


def test_connect_starts_the_trial():
    user, _ = async_to_sync(get_or_create_user)(555, "vasya", "Вася")

    text, _ = async_to_sync(connect_screen)(user)

    subscription = Subscription.objects.get(user=user)
    assert subscription.days_left >= 3
    assert text == texts.CONNECT_CHOOSE_PLATFORM, "и сразу пускаем выбирать платформу"


def test_pressing_connect_twice_does_not_extend_the_trial():
    user, _ = async_to_sync(get_or_create_user)(555, "vasya", "Вася")

    async_to_sync(connect_screen)(user)
    first = Subscription.objects.get(user=user).expires_at
    async_to_sync(connect_screen)(user)

    assert Subscription.objects.get(user=user).expires_at == first
    assert SubscriptionEvent.objects.filter(reason=SubscriptionEventReasonEnum.TRIAL).count() == 1


def test_legacy_user_gets_nothing_new():
    """Легаси пробный не полагается — у него уже есть перенесённые дни."""
    user = NexUserFactory(is_legacy=True)

    text, _ = async_to_sync(connect_screen)(user)

    assert not Subscription.objects.filter(user=user).exists()
    assert text == texts.SUBSCRIPTION_NONE


def test_expired_trial_is_not_granted_again():
    """Пробный сгорел — второй раз он не выдаётся, иначе это бесконечный VPN."""
    user, _ = async_to_sync(get_or_create_user)(555, "vasya", "Вася")
    async_to_sync(connect_screen)(user)
    subscription = Subscription.objects.get(user=user)
    Subscription.objects.filter(pk=subscription.pk).update(
        expires_at=subscription.created_at - __import__("datetime").timedelta(days=1)
    )

    text, _ = async_to_sync(connect_screen)(user)

    assert SubscriptionEvent.objects.filter(reason=SubscriptionEventReasonEnum.TRIAL).count() == 1
    assert text == texts.CONNECT_NEEDS_SUBSCRIPTION


def test_subscription_screen_does_not_say_there_is_nothing():
    """Новичок, зашедший в «Мою подписку» раньше «Подключиться», не должен
    видеть «подписки нет» — приветствие минуту назад обещало ему дни."""
    user, _ = async_to_sync(get_or_create_user)(555, "vasya", "Вася")

    view = async_to_sync(get_subscription_view)(user)
    text = build_text(view)

    assert view.trial_available is True
    assert text != texts.SUBSCRIPTION_NONE
    assert "3 дня" in text
    assert not Subscription.objects.filter(user=user).exists(), "экран не должен начинать отсчёт"


def test_subscription_screen_says_nothing_to_those_without_a_trial():
    user = NexUserFactory(is_legacy=True)

    view = async_to_sync(get_subscription_view)(user)

    assert view.trial_available is False
    assert build_text(view) == texts.SUBSCRIPTION_NONE


def test_trial_available_is_false_once_a_subscription_exists(trial_plan):
    subscription = SubscriptionFactory(plan=trial_plan)

    assert service.trial_available(subscription.user) is False


# --- истёкшая подписка: путь вперёд, а не только «назад» ---


def test_connect_screen_offers_a_way_forward_when_expired(trial_plan):
    """Человек пришёл подключаться. Тупик с одной кнопкой «назад» — плохой ответ."""
    subscription = SubscriptionFactory(
        plan=trial_plan, expires_at=__import__("django.utils.timezone", fromlist=["now"]).now()
        - __import__("datetime").timedelta(days=1)
    )

    text, keyboard = async_to_sync(connect_screen)(subscription.user)

    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert text == texts.CONNECT_NEEDS_SUBSCRIPTION
    assert any("Продлить" in label for label in labels)
    assert any("Сменить тариф" in label for label in labels)


def test_downgrade_on_an_expired_subscription_applies_at_once(trial_plan, monkeypatch):
    """Откладывать некуда: оплаченного периода нет, и отсрочка показала бы
    «перейдёшь с 05.09» с прошедшей датой."""
    import datetime as dt

    from django.utils.timezone import now

    from bot.services import change_plan_free
    from nexvpn.api.tests.factories import PlanFactory

    cheaper = PlanFactory(device_limit=1, price_month=150)
    subscription = SubscriptionFactory(plan=trial_plan, expires_at=now() - dt.timedelta(days=1))

    result = async_to_sync(change_plan_free)(subscription.user, cheaper.device_limit)

    assert result.plan_id == cheaper.pk, "тариф должен смениться сразу"
    assert result.next_plan_id is None, "ничего откладывать не нужно"


def test_downgrade_on_an_active_subscription_is_still_deferred(trial_plan):
    """А вот у живой подписки отсрочка остаётся: устройства оплачены."""
    import datetime as dt

    from django.utils.timezone import now

    from bot.services import change_plan_free
    from nexvpn.api.tests.factories import PlanFactory

    cheaper = PlanFactory(device_limit=1, price_month=150)
    subscription = SubscriptionFactory(plan=trial_plan, expires_at=now() + dt.timedelta(days=20))

    result = async_to_sync(change_plan_free)(subscription.user, cheaper.device_limit)

    assert result.plan_id == trial_plan.pk, "текущий тариф до конца периода не трогаем"
    assert result.next_plan_id == cheaper.pk
