"""«Перейти бесплатно» скрыт, если остаток по новому тарифу — 0 дней.

Остаток пересчитывается по курсу нового тарифа и округляется вниз
(`days_left * price_from // price_to`). При большом скачке цены и небольшом
остатке результат — 0. «Бесплатный» переход в этот момент — не бесплатный
переход, а способ прямо сейчас обнулить активную подписку: после фикса
15.09.2026 такое действие не дарит дней и не сдвигает срок вперёд, оно просто
завершает подписку немедленно. Предлагать это как один из двух равноправных
вариантов нечестно, поэтому кнопка и текст под неё скрыты — остаётся только
доплата за полный период.
"""

import datetime as dt

import pytest
from asgiref.sync import async_to_sync
from django.utils.timezone import now

from bot import services, texts
from bot.handlers.billing import handle_plan_details
from bot.keyboards import keyboards
from bot.keyboards.factories import PlanCallback
from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory

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

    async def answer(self, text=None, show_alert=False):
        pass


def labels(call):
    return [b.text for row in call.message.keyboard.inline_keyboard for b in row]


def make_option(**overrides):
    defaults = dict(
        device_limit=10, name="10 устройств", price_month=5000,
        is_current=False, is_upgrade=True, converted_days=0, topup_price=1200,
    )
    defaults.update(overrides)
    return services.PlanOption(**defaults)


# --- клавиатура ---


def test_free_button_hidden_when_conversion_rounds_to_zero():
    keyboard = keyboards.plan_change(make_option(converted_days=0))

    assert not any("бесплатно" in b.text.lower() for row in keyboard.inline_keyboard for b in row)
    assert any("Доплатить" in b.text or "доплат" in b.text.lower()
               for row in keyboard.inline_keyboard for b in row)


def test_free_button_shown_when_conversion_gives_at_least_a_day():
    keyboard = keyboards.plan_change(make_option(converted_days=1))

    assert any("бесплатно" in b.text.lower() for row in keyboard.inline_keyboard for b in row)


def test_downgrade_free_button_is_never_affected():
    """У понижения остаток по курсу не может уйти в 0 — это не тот путь."""
    keyboard = keyboards.plan_change(make_option(is_upgrade=False, converted_days=90, topup_price=None))

    assert any("бесплатно" in b.text.lower() for row in keyboard.inline_keyboard for b in row)


# --- экран целиком ---


def test_screen_offers_only_topup_when_conversion_is_zero():
    """Дорогой тариф, маленький остаток: пересчёт даёт 0 дней."""
    cheap = PlanFactory(device_limit=1, price_month=100)
    pricey = PlanFactory(device_limit=10, price_month=5000)
    subscription = SubscriptionFactory(plan=cheap, expires_at=now() + dt.timedelta(hours=20))

    call = FakeCall()
    async_to_sync(handle_plan_details)(
        call, PlanCallback(device_limit=pricey.device_limit, action="open"), subscription.user
    )

    assert "перейти бесплатно нельзя" in call.message.text.lower()
    assert "это 0 дней" not in call.message.text, "не должны предлагать переход на ноль дней"
    assert not any("бесплатно" in label.lower() for label in labels(call))
    assert any("доплат" in label.lower() for label in labels(call))


def test_screen_offers_the_free_option_when_conversion_is_real():
    cheap = PlanFactory(device_limit=1, price_month=100)
    modest = PlanFactory(device_limit=3, price_month=150)
    subscription = SubscriptionFactory(plan=cheap, expires_at=now() + dt.timedelta(days=30))

    call = FakeCall()
    async_to_sync(handle_plan_details)(
        call, PlanCallback(device_limit=modest.device_limit, action="open"), subscription.user
    )

    assert "ничего не доплачивая" in call.message.text
    assert any("бесплатно" in label.lower() for label in labels(call))
