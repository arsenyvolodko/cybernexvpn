"""Экран «Подключиться», когда все слоты тарифа заняты.

Панель всё равно откажет новому устройству, поэтому честнее сказать об этом
до попытки, а не после. Проверяется текст (называет тариф по имени, а не
цифрой лимита) и что путь вперёд не тупиковый: «Мои устройства», «Сменить
тариф» и «Назад», а не одна кнопка возврата.
"""

import pytest
from asgiref.sync import async_to_sync

from bot import texts
from bot.handlers.connect import connect_screen
from bot.keyboards import keyboards
from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_devices_endpoint(monkeypatch):
    """Панель не трогаем — устройства подставляем напрямую."""


def make_view_devices(monkeypatch, count):
    monkeypatch.setattr(
        "nexvpn.subscription.panel_sync.list_devices",
        lambda subscription, client=None: [{"hwid": f"h{i}"} for i in range(count)],
    )


def test_screen_names_the_plan_not_the_number(monkeypatch):
    plan = PlanFactory(device_limit=1, price_month=150, name="1 устройство")
    subscription = SubscriptionFactory(plan=plan)
    make_view_devices(monkeypatch, 1)

    text, _ = async_to_sync(connect_screen)(subscription.user)

    assert text == texts.CONNECT_NO_SLOTS.format(plan="1 устройство")
    assert "1 устройство" in text
    assert "{limit}" not in text


def test_keyboard_offers_devices_plan_and_back(monkeypatch):
    plan = PlanFactory(device_limit=1, price_month=150, name="1 устройство")
    subscription = SubscriptionFactory(plan=plan)
    make_view_devices(monkeypatch, 1)

    _, keyboard = async_to_sync(connect_screen)(subscription.user)

    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert any("Мои устройства" in label for label in labels)
    assert any("Сменить тариф" in label for label in labels)
    assert any("Назад" in label for label in labels)
    assert len(labels) == 3, "ровно три кнопки, не больше и не меньше"


def test_below_the_limit_still_offers_to_connect(monkeypatch):
    plan = PlanFactory(device_limit=3, price_month=400, name="3 устройства")
    subscription = SubscriptionFactory(plan=plan)
    make_view_devices(monkeypatch, 1)

    text, keyboard = async_to_sync(connect_screen)(subscription.user)

    assert text == texts.CONNECT_CHOOSE_PLATFORM
