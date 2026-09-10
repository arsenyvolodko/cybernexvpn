"""Запрет добавлять устройства сверх лимита — опросом, а не вебхуком.

Панель заявляет событие `user_hwid_devices.added` в своём API, но эмпирически
(проверено 10.09.2026 на 72+ часах живого трафика с реальными добавлениями
устройств) ни разу его не прислала — только `.deleted` и `user.modified`.
Вебхук-версия запрета (`_enforce_device_limit` в remnawave.py) от этого не
срабатывает никогда: событие, на которое она подписана, не приходит. Здесь —
рабочая замена: сравниваем текущий список устройств с тем, что видели в
прошлый раз, и снимаем только то, что добавилось с тех пор.
"""

import pytest

from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory
from nexvpn.models import Subscription
from nexvpn.subscription import panel_sync

pytestmark = pytest.mark.django_db


def device(hwid, created="2026-09-01T00:00:00.000Z"):
    return {"hwid": hwid, "deviceModel": hwid, "createdAt": created, "updatedAt": created}


@pytest.fixture
def panel(monkeypatch):
    state = {"devices": [], "deleted": []}

    def list_devices(subscription, client=None):
        return list(state["devices"])

    def remove_device(subscription, hwid, client=None):
        state["deleted"].append(hwid)
        state["devices"] = [d for d in state["devices"] if d["hwid"] != hwid]

    monkeypatch.setattr(panel_sync, "list_devices", list_devices)
    monkeypatch.setattr(panel_sync, "remove_device", remove_device)
    return state


@pytest.fixture
def subscription():
    plan = PlanFactory(device_limit=1, price_month=150)
    return SubscriptionFactory(plan=plan, panel_user_id=999)


def test_first_observation_only_seeds_the_baseline(panel, subscription):
    """Первая проверка не наказывает за то, что уже было — даже если это
    больше лимита. С этим разговор отдельный, человеческий."""
    panel["devices"] = [device("A"), device("B"), device("C")]

    removed = panel_sync.reject_devices_added_over_limit(subscription)

    assert removed is None
    assert panel["deleted"] == []
    stored = Subscription.objects.get(pk=subscription.pk)
    assert set(stored.known_device_hwids) == {"A", "B", "C"}


def test_new_device_beyond_the_limit_is_rejected(panel, subscription):
    """Второй проход: то же, что было, плюс одно новое — новое снимается."""
    panel["devices"] = [device("A")]
    panel_sync.reject_devices_added_over_limit(subscription)
    subscription.refresh_from_db()

    panel["devices"] = [device("A"), device("B", created="2026-09-10T12:00:00.000Z")]

    removed = panel_sync.reject_devices_added_over_limit(subscription)

    assert [d["hwid"] for d in removed] == ["B"]
    assert panel["deleted"] == ["B"]
    assert panel["devices"] == [device("A")]


def test_old_device_is_never_touched_by_a_new_addition(panel, subscription):
    """Снимается новое, а не старое — старое не виновато, что кто-то добавил лишнее."""
    panel["devices"] = [device("OLD")]
    panel_sync.reject_devices_added_over_limit(subscription)
    subscription.refresh_from_db()
    panel["devices"] = [device("OLD"), device("NEW", created="2026-09-10T12:00:00.000Z")]

    panel_sync.reject_devices_added_over_limit(subscription)

    assert panel["deleted"] == ["NEW"]
    assert any(d["hwid"] == "OLD" for d in panel["devices"])


def test_staying_over_the_limit_without_new_devices_is_left_alone(panel, subscription):
    """Сверх лимита давно, но нового ничего не появилось — не наше дело."""
    panel["devices"] = [device("A"), device("B")]
    panel_sync.reject_devices_added_over_limit(subscription)
    subscription.refresh_from_db()

    removed = panel_sync.reject_devices_added_over_limit(subscription)

    assert removed is None
    assert panel["deleted"] == []


def test_removes_only_as_many_as_needed(panel):
    """Лимит на три, добавили сразу два новых — снимается ровно один лишний."""
    plan = PlanFactory(device_limit=3, price_month=400)
    subscription = SubscriptionFactory(plan=plan)
    panel["devices"] = [device("A"), device("B")]
    panel_sync.reject_devices_added_over_limit(subscription)
    subscription.refresh_from_db()

    panel["devices"] = [
        device("A"), device("B"),
        device("C", created="2026-09-10T10:00:00.000Z"),
        device("D", created="2026-09-10T11:00:00.000Z"),
    ]

    removed = panel_sync.reject_devices_added_over_limit(subscription)

    assert len(removed) == 1
    assert len(panel["devices"]) == 3


def test_device_removed_elsewhere_updates_the_baseline(panel, subscription):
    """Человек сам удалил устройство — не должно потом считаться «пропавшим новым»."""
    panel["devices"] = [device("A"), device("B")]
    panel_sync.reject_devices_added_over_limit(subscription)
    subscription.refresh_from_db()

    panel["devices"] = [device("A")]
    panel_sync.reject_devices_added_over_limit(subscription)
    subscription.refresh_from_db()

    assert subscription.known_device_hwids == ["A"]
    assert panel["deleted"] == []


def test_within_the_limit_nothing_happens(panel):
    plan = PlanFactory(device_limit=3, price_month=400)
    subscription = SubscriptionFactory(plan=plan)
    panel["devices"] = [device("A")]
    panel_sync.reject_devices_added_over_limit(subscription)
    subscription.refresh_from_db()

    panel["devices"] = [device("A"), device("B", created="2026-09-10T12:00:00.000Z")]
    removed = panel_sync.reject_devices_added_over_limit(subscription)

    assert removed is None
    assert panel["deleted"] == []


# --- задача целиком ---


def test_task_notifies_after_removing(panel, subscription, monkeypatch, settings):
    from nexvpn import tasks

    settings.DEBUG = False
    panel["devices"] = [device("A")]
    panel_sync.reject_devices_added_over_limit(subscription)
    subscription.refresh_from_db()
    panel["devices"] = [device("A"), device("B", created="2026-09-10T12:00:00.000Z")]

    sent = []
    monkeypatch.setattr("bot.notify.notify_device_limit_reached",
                        lambda chat_id, devices: sent.append((chat_id, devices)) or True)

    result = tasks.enforce_device_limits()

    assert result["removed"] == 1
    assert result["notified"] == 1
    assert sent[0][0] == subscription.user_id


def test_task_does_nothing_when_debug_is_on(panel, subscription, settings):
    from nexvpn import tasks

    settings.DEBUG = True
    panel["devices"] = [device("A"), device("B", created="2026-09-10T12:00:00.000Z")]

    result = tasks.enforce_device_limits()

    assert result == {"skipped": True}
    assert panel["deleted"] == [], "боевую панель с dev-машины трогать нельзя"


def test_task_survives_a_silent_panel(subscription, monkeypatch, settings):
    from nexvpn import tasks
    from nexvpn.remnawave import RemnawaveError

    settings.DEBUG = False
    monkeypatch.setattr(
        panel_sync, "list_devices",
        lambda *a, **kw: (_ for _ in ()).throw(RemnawaveError("нет связи")),
    )

    result = tasks.enforce_device_limits()

    assert result["failed"] >= 1
