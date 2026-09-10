"""Приём вебхуков Remnawave: подпись, повторы, уведомление о подключении."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from django.test import override_settings

from nexvpn.api.tests.factories import NexUserFactory, PlanFactory, SubscriptionFactory
from nexvpn.models import DeviceConnectionWatch

pytestmark = pytest.mark.django_db

SECRET = "test-secret"
URL = "/api/v1/remnawave/webhook/"
PANEL_USER_ID = 42


def payload(event="user_hwid_devices.added", *, hwid="NEW-HWID", stamp=None, **extra):
    return {
        "scope": "user_hwid_devices",
        "event": event,
        "timestamp": (stamp or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
        "data": {"userId": PANEL_USER_ID, "hwid": hwid, "deviceModel": "iPhone 15", **extra},
    }


def post(client, body: dict, *, signature: str | None = None, secret: str = SECRET):
    raw = json.dumps(body).encode()
    if signature is None:
        signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        URL, data=raw, content_type="application/json", HTTP_X_REMNAWAVE_SIGNATURE=signature
    )


@pytest.fixture
def watching_user():
    """Пользователь, который прямо сейчас ждёт подключения устройства."""
    user = NexUserFactory(id=777)
    subscription = SubscriptionFactory(user=user, plan=PlanFactory(device_limit=3, price_month=400))
    subscription.panel_user_id = PANEL_USER_ID
    subscription.save()
    DeviceConnectionWatch.objects.create(
        user=user, chat_id=777, message_id=10, known_hwids=["OLD-HWID"]
    )
    return user


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "bot.notify.notify_device_connected",
        lambda **kwargs: calls.append(kwargs),
    )
    return calls


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_valid_signature_triggers_notification(client, watching_user, sent):
    assert post(client, payload()).status_code == 200

    assert len(sent) == 1
    assert sent[0]["chat_id"] == 777 and sent[0]["message_id"] == 10
    assert sent[0]["device_title"] == "iPhone 15"


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_wrong_signature_is_rejected(client, watching_user, sent):
    assert post(client, payload(), signature="deadbeef").status_code == 403
    assert sent == []


@override_settings(REMNAWAVE_WEBHOOK_SECRET="")
def test_without_configured_secret_nothing_is_accepted(client, watching_user, sent):
    """Пустой секрет — не «пропускать всё», а «не принимать ничего»."""
    assert post(client, payload(), secret="").status_code == 403
    assert sent == []


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_stale_delivery_is_ignored(client, watching_user, sent):
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    assert post(client, payload(stamp=old)).status_code == 200
    assert sent == []


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_watch_is_consumed_once(client, watching_user, sent):
    """Замок: повторная доставка того же события не шлёт второе сообщение."""
    post(client, payload())
    post(client, payload())

    assert len(sent) == 1
    assert not DeviceConnectionWatch.objects.filter(user=watching_user).exists()


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_device_that_was_already_there_is_ignored(client, watching_user, sent):
    assert post(client, payload(hwid="OLD-HWID")).status_code == 200
    assert sent == []


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_nobody_waiting_means_no_message(client, sent):
    assert post(client, payload()).status_code == 200
    assert sent == []


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_other_events_are_accepted_quietly(client, watching_user, sent):
    assert post(client, payload(event="user.modified")).status_code == 200
    assert sent == []
    assert DeviceConnectionWatch.objects.filter(user=watching_user).exists()


# --- срок правят прямо в панели ---


def modified_payload(expire_at, *, panel_user_id=PANEL_USER_ID, **extra):
    return {
        "scope": "user",
        "event": "user.modified",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "id": panel_user_id,
            "username": "tg_777",
            "expireAt": expire_at.isoformat().replace("+00:00", "Z"),
            **extra,
        },
    }


@pytest.fixture
def synced_subscription():
    from nexvpn.enums import PanelSyncStatusEnum

    return SubscriptionFactory(
        plan=PlanFactory(device_limit=3, price_month=400),
        panel_user_id=PANEL_USER_ID,
        panel_status=PanelSyncStatusEnum.SYNCED,
        expires_at=datetime.now(timezone.utc) + timedelta(days=10),
    )


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_panel_change_is_accepted(client, synced_subscription):
    from nexvpn.models import Subscription

    new_date = synced_subscription.expires_at + timedelta(days=30)

    assert post(client, modified_payload(new_date)).status_code == 200

    stored = Subscription.objects.get(pk=synced_subscription.pk)
    assert abs(stored.expires_at - new_date) < timedelta(seconds=1)


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_panel_change_is_written_into_history(client, synced_subscription):
    """Дата без объяснения — худшее, что можно оставить в биллинге."""
    from nexvpn.enums import SubscriptionEventReasonEnum
    from nexvpn.models import SubscriptionEvent

    before = synced_subscription.expires_at
    new_date = before + timedelta(days=30)

    post(client, modified_payload(new_date))

    event = SubscriptionEvent.objects.get(user=synced_subscription.user)
    assert event.reason == SubscriptionEventReasonEnum.ADMIN_ADJUSTMENT
    assert event.comment == "Изменено в панели"
    assert event.delta_days == 30
    assert abs(event.expires_at_before - before) < timedelta(seconds=1)


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_our_own_change_does_not_come_back_as_a_loop(client, synced_subscription):
    """Мы пишем в панель, панель шлёт событие — на нём нельзя писать себе снова."""
    from nexvpn.models import SubscriptionEvent

    post(client, modified_payload(synced_subscription.expires_at))

    assert not SubscriptionEvent.objects.exists()


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_pending_subscription_keeps_our_value(client):
    """У нас правка в очереди — она всё равно перезапишет панель.

    Подхватить в этот момент значение панели значит потерять своё изменение.
    """
    from nexvpn.enums import PanelSyncStatusEnum
    from nexvpn.models import Subscription

    subscription = SubscriptionFactory(
        plan=PlanFactory(device_limit=3, price_month=400),
        panel_user_id=PANEL_USER_ID,
        panel_status=PanelSyncStatusEnum.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(days=10),
    )
    ours = subscription.expires_at

    post(client, modified_payload(ours + timedelta(days=30)))

    assert Subscription.objects.get(pk=subscription.pk).expires_at == ours


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_reminders_reset_after_a_panel_extension(client, synced_subscription):
    """Период сдвинулся — предупреждать о новом окончании надо заново."""
    from nexvpn.models import SentReminder

    SentReminder.objects.create(
        subscription=synced_subscription, hours_before=24,
        expires_at=synced_subscription.expires_at,
    )

    post(client, modified_payload(synced_subscription.expires_at + timedelta(days=30)))

    assert not SentReminder.objects.exists()


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_unknown_panel_user_is_ignored(client, synced_subscription):
    from nexvpn.models import SubscriptionEvent

    body = modified_payload(synced_subscription.expires_at + timedelta(days=5), panel_user_id=99999)

    assert post(client, body).status_code == 200
    assert not SubscriptionEvent.objects.exists()


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_broken_date_does_not_break_the_webhook(client, synced_subscription):
    from nexvpn.models import Subscription

    body = modified_payload(synced_subscription.expires_at)
    body["data"]["expireAt"] = "позавчера"

    assert post(client, body).status_code == 200
    assert Subscription.objects.get(pk=synced_subscription.pk).expires_at == synced_subscription.expires_at


# --- запрет добавлять устройства сверх лимита ---


def hwid_payload(hwid, panel_user_id=PANEL_USER_ID):
    return {
        "scope": "user_hwid_devices",
        "event": "user_hwid_devices.added",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {"userId": panel_user_id, "hwid": hwid, "deviceModel": "iPhone 15"},
    }


@pytest.fixture
def panel_with_devices(monkeypatch):
    """Панель с управляемым списком устройств, удаление отслеживается."""
    state = {"devices": [], "deleted": []}

    def list_devices(subscription, client=None):
        return state["devices"]

    def remove_device(subscription, hwid, client=None):
        state["deleted"].append(hwid)
        state["devices"] = [d for d in state["devices"] if d["hwid"] != hwid]

    monkeypatch.setattr("nexvpn.subscription.panel_sync.list_devices", list_devices)
    monkeypatch.setattr("nexvpn.subscription.panel_sync.remove_device", remove_device)
    return state


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_extra_device_over_the_limit_is_removed(client, panel_with_devices, monkeypatch):
    from nexvpn.enums import PanelSyncStatusEnum

    monkeypatch.setattr("bot.notify.notify_device_limit_reached", lambda *a, **kw: True)
    subscription = SubscriptionFactory(
        plan=PlanFactory(device_limit=1, price_month=150),
        panel_user_id=PANEL_USER_ID, panel_status=PanelSyncStatusEnum.SYNCED,
    )
    panel_with_devices["devices"] = [
        {"hwid": "OLD", "deviceModel": "Старый телефон"},
        {"hwid": "NEW", "deviceModel": "Новый телефон"},
    ]

    assert post(client, hwid_payload("NEW")).status_code == 200

    assert panel_with_devices["deleted"] == ["NEW"], "снимаем именно новое, а не старое"
    assert [d["hwid"] for d in panel_with_devices["devices"]] == ["OLD"]


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_device_within_the_limit_is_left_alone(client, panel_with_devices):
    from nexvpn.enums import PanelSyncStatusEnum

    SubscriptionFactory(
        plan=PlanFactory(device_limit=3, price_month=400),
        panel_user_id=PANEL_USER_ID, panel_status=PanelSyncStatusEnum.SYNCED,
    )
    panel_with_devices["devices"] = [{"hwid": "NEW", "deviceModel": "Телефон"}]

    assert post(client, hwid_payload("NEW")).status_code == 200

    assert panel_with_devices["deleted"] == []


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_user_gets_told_which_devices_remain(client, panel_with_devices, monkeypatch):
    from nexvpn.enums import PanelSyncStatusEnum

    sent = {}
    monkeypatch.setattr(
        "bot.notify.notify_device_limit_reached",
        lambda chat_id, devices: sent.update(chat_id=chat_id, devices=devices) or True,
    )
    subscription = SubscriptionFactory(
        plan=PlanFactory(device_limit=1, price_month=150),
        panel_user_id=PANEL_USER_ID, panel_status=PanelSyncStatusEnum.SYNCED,
    )
    panel_with_devices["devices"] = [
        {"hwid": "OLD", "deviceModel": "Старый телефон"},
        {"hwid": "NEW", "deviceModel": "Новый телефон"},
    ]

    post(client, hwid_payload("NEW"))

    assert sent["chat_id"] == subscription.user_id
    assert sent["devices"] == ["Старый телефон"], "новое уже снято, показываем то, что осталось"


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_missing_hwid_does_not_remove_anything(client, panel_with_devices):
    from nexvpn.enums import PanelSyncStatusEnum

    SubscriptionFactory(
        plan=PlanFactory(device_limit=1, price_month=150),
        panel_user_id=PANEL_USER_ID, panel_status=PanelSyncStatusEnum.SYNCED,
    )
    panel_with_devices["devices"] = [
        {"hwid": "OLD", "deviceModel": "Старый"}, {"hwid": "NEW", "deviceModel": "Новый"},
    ]
    body = hwid_payload("NEW")
    del body["data"]["hwid"]

    assert post(client, body).status_code == 200
    assert panel_with_devices["deleted"] == []


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_silent_panel_does_not_remove_anything(client, monkeypatch):
    from nexvpn.enums import PanelSyncStatusEnum
    from nexvpn.remnawave import RemnawaveError

    monkeypatch.setattr(
        "nexvpn.subscription.panel_sync.list_devices",
        lambda *a, **kw: (_ for _ in ()).throw(RemnawaveError("нет связи")),
    )
    SubscriptionFactory(
        plan=PlanFactory(device_limit=1, price_month=150),
        panel_user_id=PANEL_USER_ID, panel_status=PanelSyncStatusEnum.SYNCED,
    )

    assert post(client, hwid_payload("NEW")).status_code == 200


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_watch_flow_still_works_when_within_the_limit(client, panel_with_devices, watching_user, sent):
    """Обычное подключение (в пределах лимита) по-прежнему сообщает об успехе."""
    panel_with_devices["devices"] = [{"hwid": "NEW-HWID", "deviceModel": "iPhone 15"}]

    assert post(client, payload()).status_code == 200

    assert len(sent) == 1
    assert panel_with_devices["deleted"] == []


@override_settings(REMNAWAVE_WEBHOOK_SECRET=SECRET)
def test_rejected_device_does_not_also_announce_success(client, panel_with_devices, watching_user, sent, monkeypatch):
    """Устройство сняли за лимит — экран ожидания не должен сказать «готово»."""
    from nexvpn.enums import PanelSyncStatusEnum
    from nexvpn.models import Subscription

    monkeypatch.setattr("bot.notify.notify_device_limit_reached", lambda *a, **kw: True)

    # Тот же пользователь, что ждёт подключения: подгоняем его тариф под
    # лимит в один слот, а не заводим вторую подписку на тот же panel_user_id
    # (иначе .filter(panel_user_id=...).first() может достать не ту).
    Subscription.objects.filter(user=watching_user).update(
        plan=PlanFactory(device_limit=1, price_month=150), panel_status=PanelSyncStatusEnum.SYNCED,
    )
    panel_with_devices["devices"] = [
        {"hwid": "OLD", "deviceModel": "Старый"}, {"hwid": "NEW-HWID", "deviceModel": "iPhone 15"},
    ]

    post(client, payload())

    assert sent == [], "не два сообщения сразу — только про лимит"
    assert panel_with_devices["deleted"] == ["NEW-HWID"]
