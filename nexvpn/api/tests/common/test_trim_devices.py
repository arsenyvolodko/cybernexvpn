"""Переход на тариф с меньшим числом устройств.

Панель лишние HWID сама не выбрасывает — она лишь перестаёт принимать новые.
Без этого сценария человек оставался в состоянии «Занято: 3 из 1»: тариф
уменьшился, устройства висят, подключить новое нельзя.
"""

import datetime as dt

import pytest
from asgiref.sync import async_to_sync
from django.utils.timezone import now

from bot import services, texts
from bot.handlers import trim
from bot.keyboards.factories import TrimCallback
from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory
from nexvpn.models import PlanChangeSelection, Subscription

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


def device(hwid, title, days_ago):
    stamp = (now() - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {"hwid": hwid, "deviceModel": title, "platform": "iOS",
            "createdAt": stamp, "updatedAt": stamp}


@pytest.fixture
def panel(monkeypatch):
    """Панель с тремя устройствами. Удаления записываются, а не выполняются."""
    state = {
        "devices": [
            device("A", "Свежий", 0),
            device("B", "Средний", 5),
            device("C", "Древний", 40),
        ],
        "deleted": [],
    }

    def list_devices(subscription, client=None):
        return [d for d in state["devices"] if d["hwid"] not in state["deleted"]]

    def remove_device(subscription, hwid, client=None):
        state["deleted"].append(hwid)

    monkeypatch.setattr("nexvpn.subscription.panel_sync.list_devices", list_devices)
    monkeypatch.setattr("nexvpn.subscription.panel_sync.remove_device", remove_device)
    monkeypatch.setattr("bot.services._sync_quietly", lambda subscription: None)
    return state


@pytest.fixture
def small():
    return PlanFactory(device_limit=1, price_month=150)


@pytest.fixture
def expired_subscription(small):
    big = PlanFactory(device_limit=3, price_month=400)
    return SubscriptionFactory(plan=big, expires_at=now() - dt.timedelta(days=1))


def call_step(action, user, token=""):
    call = FakeCall()
    handlers = {
        "auto": trim.handle_auto, "manual": trim.handle_manual,
        "apply": trim.handle_apply, "warn": trim.handle_back_to_warning,
    }
    if action == "toggle":
        async_to_sync(trim.handle_toggle)(call, TrimCallback(action="toggle", token=token), user)
    else:
        async_to_sync(handlers[action])(call, user)
    return call


def labels(call):
    return [b.text for row in call.message.keyboard.inline_keyboard for b in row]


def test_warning_lists_who_would_go(panel, expired_subscription, small):
    call = FakeCall()

    shown = async_to_sync(trim.show_warning)(call, expired_subscription.user, small.device_limit)

    assert shown is True
    assert "Древний" in call.message.text and "Средний" in call.message.text
    assert "Свежий" not in call.message.text, "самое свежее остаётся, о нём речи нет"


def test_no_warning_when_devices_fit(panel, expired_subscription):
    """Устройств не больше лимита — разговаривать не о чем."""
    roomy = PlanFactory(device_limit=5, price_month=900)
    call = FakeCall()

    assert async_to_sync(trim.show_warning)(call, expired_subscription.user, roomy.device_limit) is False


def test_auto_removes_the_least_recently_used(panel, expired_subscription, small):
    async_to_sync(trim.show_warning)(FakeCall(), expired_subscription.user, small.device_limit)

    call_step("auto", expired_subscription.user)

    assert sorted(panel["deleted"]) == ["B", "C"], "остаётся самое свежее"
    assert Subscription.objects.get(pk=expired_subscription.pk).plan_id == small.pk


def test_apply_refuses_until_enough_is_picked(panel, expired_subscription, small):
    async_to_sync(trim.show_warning)(FakeCall(), expired_subscription.user, small.device_limit)
    call_step("manual", expired_subscription.user)
    call_step("toggle", expired_subscription.user, services._device_token("C"))

    call = call_step("apply", expired_subscription.user)

    assert call.alerts, "должно быть окошко, а не молчание"
    assert "ещё" in call.alerts[0]
    assert panel["deleted"] == [], "ничего не удалено"
    assert Subscription.objects.get(pk=expired_subscription.pk).plan_id != small.pk


def test_picked_devices_are_marked(panel, expired_subscription, small):
    async_to_sync(trim.show_warning)(FakeCall(), expired_subscription.user, small.device_limit)
    call_step("manual", expired_subscription.user)

    call = call_step("toggle", expired_subscription.user, services._device_token("C"))

    marked = [label for label in labels(call) if label.startswith("✖")]
    assert len(marked) == 1 and "Древний" in marked[0]
    assert "Осталось выбрать" in call.message.text


def test_choice_can_be_taken_back(panel, expired_subscription, small):
    async_to_sync(trim.show_warning)(FakeCall(), expired_subscription.user, small.device_limit)
    call_step("manual", expired_subscription.user)
    token = services._device_token("C")
    call_step("toggle", expired_subscription.user, token)

    call = call_step("toggle", expired_subscription.user, token)

    assert not [label for label in labels(call) if label.startswith("✖")]


def test_manual_choice_is_applied(panel, expired_subscription, small):
    async_to_sync(trim.show_warning)(FakeCall(), expired_subscription.user, small.device_limit)
    call_step("manual", expired_subscription.user)
    call_step("toggle", expired_subscription.user, services._device_token("A"))
    call_step("toggle", expired_subscription.user, services._device_token("B"))

    call = call_step("apply", expired_subscription.user)

    assert sorted(panel["deleted"]) == ["A", "B"], "удаляем выбранное, а не самое давнее"
    assert Subscription.objects.get(pk=expired_subscription.pk).plan_id == small.pk
    assert "Готово" in call.message.text
    assert not PlanChangeSelection.objects.exists(), "выбор после применения не нужен"


def test_counter_counts_down(panel, expired_subscription, small):
    async_to_sync(trim.show_warning)(FakeCall(), expired_subscription.user, small.device_limit)
    call_step("manual", expired_subscription.user)

    first = call_step("toggle", expired_subscription.user, services._device_token("C"))
    assert "1" in first.message.text

    second = call_step("toggle", expired_subscription.user, services._device_token("B"))
    assert "Выбрано достаточно" in second.message.text


def test_silent_panel_does_not_change_the_plan(monkeypatch, expired_subscription, small):
    from nexvpn.remnawave import RemnawaveError

    def boom(subscription, client=None):
        raise RemnawaveError("панель недоступна")

    monkeypatch.setattr("nexvpn.subscription.panel_sync.list_devices", boom)
    call = FakeCall()

    shown = async_to_sync(trim.show_warning)(call, expired_subscription.user, small.device_limit)

    assert shown is True, "молча менять тариф вслепую нельзя"
    assert texts.TRIM_PANEL_SILENT in call.message.text
    assert Subscription.objects.get(pk=expired_subscription.pk).plan_id != small.pk
