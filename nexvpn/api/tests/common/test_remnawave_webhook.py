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
