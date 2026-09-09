"""Короткий сценарий для истёкшей подписки.

Общий сценарий смены тарифа считает остаток дней, показывает подтверждение и
спрашивает про доплату. У истёкшей подписки остатка нет, и все эти шаги —
лишние нажатия между человеком и оплатой.

Проверяется путь целиком: из сообщения об окончании в тарифы, оттуда обратно,
и от смены тарифа до экрана оплаты.
"""

import datetime as dt

import pytest
from asgiref.sync import async_to_sync
from django.utils.timezone import now

from bot import texts
from bot.handlers.expired import handle_expired_step
from bot.keyboards.factories import ExpiredCallback
from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory
from nexvpn.models import Subscription

pytestmark = pytest.mark.django_db


class FakeChat:
    id = 100


class FakeMessage:
    def __init__(self):
        self.chat = FakeChat()
        self.message_id = 7
        self.photo = self.document = self.video = None
        self.text = None
        self.keyboard = None

    async def edit_text(self, text, reply_markup=None):
        self.text, self.keyboard = text, reply_markup

    async def answer(self, text, reply_markup=None):
        self.text, self.keyboard = text, reply_markup
        return self

    async def delete(self):
        pass

    async def edit_reply_markup(self, reply_markup=None):
        pass


class FakeCall:
    def __init__(self):
        self.message = FakeMessage()
        self.alerts = []

    async def answer(self, text=None, show_alert=False):
        if text:
            self.alerts.append(text)


class FakeState:
    async def set_state(self, *a, **kw):
        pass

    async def update_data(self, **kw):
        pass


@pytest.fixture(autouse=True)
def no_panel(monkeypatch):
    """Смена тарифа синхронизирует подписку с панелью — в тестах туда нельзя.

    Локальный `.env` смотрит на боевую панель, и без этой заглушки прогон
    заводил там настоящих пользователей. Один раз уже завёл.
    """
    monkeypatch.setattr("bot.services._sync_quietly", lambda subscription: None)


@pytest.fixture
def plan():
    return PlanFactory(device_limit=3, price_month=400)


@pytest.fixture
def expired_subscription(plan):
    from nexvpn.models import BillingPeriod

    BillingPeriod.objects.get_or_create(months=1, defaults={"discount_percent": 0})
    return SubscriptionFactory(plan=plan, expires_at=now() - dt.timedelta(days=1))


def step(name, subscription, device_limit=0):
    call = FakeCall()
    async_to_sync(handle_expired_step)(
        call, ExpiredCallback(step=name, device_limit=device_limit), subscription.user, FakeState()
    )
    return call


def labels(call):
    return [b.text for row in call.message.keyboard.inline_keyboard for b in row]


def callbacks(call):
    return [b.callback_data for row in call.message.keyboard.inline_keyboard for b in row]


def test_home_is_the_same_message_the_person_received(expired_subscription):
    """«Назад» должен вернуть ровно туда, откуда человек ушёл."""
    call = step("home", expired_subscription)

    assert call.message.text == texts.SUBSCRIPTION_ENDED
    assert labels(call) == ["Продлить подписку 💳", "Сменить тариф 🔄"]
    # Проверяем не подписи, а куда ведут: подписи были верными и тогда, когда
    # кнопки уходили в общий сценарий.
    assert callbacks(call) == [
        ExpiredCallback(step="renew").pack(),
        ExpiredCallback(step="plans").pack(),
    ]


def test_plans_list_has_no_confirmation_step(expired_subscription):
    """Нажатие на тариф применяет его сразу, а не открывает экран «точно?»."""
    PlanFactory(device_limit=1, price_month=150)

    call = step("plans", expired_subscription)

    picks = [c for c in callbacks(call) if c and c.startswith("exp:")]
    assert any("pick" in c for c in picks), "кнопки тарифов ведут прямо в применение"
    assert not any(c and c.startswith("plan:") for c in callbacks(call)), "общий сценарий тут не нужен"


def test_back_from_plans_returns_to_the_ended_message(expired_subscription):
    call = step("plans", expired_subscription)

    back = [c for c in callbacks(call) if c == ExpiredCallback(step="home").pack()]
    assert back, "«Назад» ведёт к сообщению об окончании, а не в меню"


def test_back_from_renew_returns_to_the_ended_message(expired_subscription):
    call = step("renew", expired_subscription)

    assert ExpiredCallback(step="home").pack() in callbacks(call)


def test_picking_a_plan_changes_it_at_once(expired_subscription):
    cheaper = PlanFactory(device_limit=1, price_month=150)

    call = step("pick", expired_subscription, device_limit=cheaper.device_limit)

    stored = Subscription.objects.get(pk=expired_subscription.pk)
    assert stored.plan_id == cheaper.pk
    assert stored.next_plan_id is None, "откладывать у истёкшей нечего"
    assert call.message.text == texts.PLAN_CHANGED_EXPIRED


def test_after_the_change_there_is_exactly_one_button(expired_subscription):
    cheaper = PlanFactory(device_limit=1, price_month=150)

    call = step("pick", expired_subscription, device_limit=cheaper.device_limit)

    assert labels(call) == ["Продлить подписку 💳"], "ни «назад», ни «сменить ещё раз»"
    assert callbacks(call) == [ExpiredCallback(step="renew_final").pack()]


def test_the_last_renew_screen_has_no_way_back(expired_subscription):
    """Человек уже выбрал тариф — возвращать его к «тариф сменён» некуда."""
    call = step("renew_final", expired_subscription)

    assert not any("Назад" in label for label in labels(call))
    assert not any("меню" in label.lower() for label in labels(call))
    assert any("мес." in label for label in labels(call)), "сроки продления на месте"


def test_unknown_step_does_not_dead_end(expired_subscription):
    call = step("чтоэто", expired_subscription)

    assert call.message.text == texts.SUBSCRIPTION_ENDED
