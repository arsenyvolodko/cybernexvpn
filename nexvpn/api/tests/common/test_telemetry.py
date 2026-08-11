"""Учёт использования: снимки из панели и выводы из них.

Главное, что здесь проверяется, — прирост трафика считается между снимками и
приписывается той ноде, где человек был. Ошибка тут тихая: цифры в админке
просто окажутся неверными, и заметить это будет нечем.
"""

from datetime import timedelta

import pytest
from django.utils.timezone import now

from nexvpn import telemetry
from nexvpn.api.tests.factories import SubscriptionFactory
from nexvpn.enums import PanelSyncStatusEnum
from nexvpn.models import NexUser, NodeUsageDay, PanelPresence
from nexvpn.remnawave.client import RemnawaveError

pytestmark = pytest.mark.django_db

NODE_UUID = "11111111-1111-1111-1111-111111111111"
OTHER_UUID = "22222222-2222-2222-2222-222222222222"


class FakePanel:
    """Панель, которой можно подсунуть нужный ответ."""

    def __init__(self, users):
        self.users = users

    def list_nodes(self):
        return [
            {"uuid": NODE_UUID, "name": "eu2-alexhost"},
            {"uuid": OTHER_UUID, "name": "eu1-ovh"},
        ]

    def iter_users(self):
        return self.users


def panel_user(panel_id, used, node=NODE_UUID, online_at=None, first_connected_at=None):
    return {
        "id": panel_id,
        "userTraffic": {
            "usedTrafficBytes": used,
            "onlineAt": online_at or now().isoformat(),
            "firstConnectedAt": first_connected_at or now().isoformat(),
            "lastConnectedNodeUuid": node,
        },
    }


def test_first_snapshot_is_only_a_baseline():
    """Нельзя записать весь накопленный трафик как прирост первого прогона."""
    subscription = SubscriptionFactory(panel_user_id=7)

    telemetry.take_snapshot(FakePanel([panel_user(7, used=5_000_000)]))

    presence = PanelPresence.objects.get(user=subscription.user)
    assert presence.used_traffic == 5_000_000
    assert presence.node_name == "eu2-alexhost"
    assert NodeUsageDay.objects.get().bytes == 0


def test_second_snapshot_records_the_increment():
    subscription = SubscriptionFactory(panel_user_id=7)

    telemetry.take_snapshot(FakePanel([panel_user(7, used=1_000)]))
    telemetry.take_snapshot(FakePanel([panel_user(7, used=3_500)]))

    row = NodeUsageDay.objects.get(user=subscription.user, node_name="eu2-alexhost")
    assert row.bytes == 2_500
    assert row.samples == 2


def test_increment_goes_to_the_node_the_user_is_on():
    subscription = SubscriptionFactory(panel_user_id=7)

    telemetry.take_snapshot(FakePanel([panel_user(7, used=1_000)]))
    telemetry.take_snapshot(FakePanel([panel_user(7, used=4_000, node=OTHER_UUID)]))

    rows = {r.node_name: r.bytes for r in NodeUsageDay.objects.filter(user=subscription.user)}
    assert rows == {"eu2-alexhost": 0, "eu1-ovh": 3_000}


def test_counter_reset_does_not_produce_negative_traffic():
    """Панель обнуляет счётчик при сбросе лимита — это не минус трафика."""
    SubscriptionFactory(panel_user_id=7)

    telemetry.take_snapshot(FakePanel([panel_user(7, used=9_000)]))
    telemetry.take_snapshot(FakePanel([panel_user(7, used=10)]))

    assert all(row.bytes >= 0 for row in NodeUsageDay.objects.all())
    assert PanelPresence.objects.get().used_traffic == 10


def test_users_outside_our_database_are_counted_not_crashed():
    """В панели живёт служебный phonetest, которого у нас нет."""
    result = telemetry.take_snapshot(FakePanel([panel_user(999, used=1)]))

    assert result.unknown_users == 1
    assert result.seen == 0


def test_never_connected_user_is_flagged_separately():
    subscription = SubscriptionFactory(panel_user_id=7)
    PanelPresence.objects.create(user=subscription.user, online_at=None, first_connected_at=None)

    silent = telemetry.silent_users()

    assert [row["never_connected"] for row in silent] == [True]


def test_recently_online_user_is_not_silent():
    subscription = SubscriptionFactory(panel_user_id=7)
    PanelPresence.objects.create(
        user=subscription.user, online_at=now() - timedelta(minutes=5), first_connected_at=now()
    )

    assert telemetry.silent_users() == []


def test_expired_subscription_is_not_silent():
    """У истёкшей подписки трафика нет по совершенно понятной причине."""
    subscription = SubscriptionFactory(panel_user_id=7, expires_at=now() - timedelta(days=1))
    PanelPresence.objects.create(user=subscription.user, online_at=None, first_connected_at=None)

    assert telemetry.silent_users() == []


def test_dashboard_page_opens(admin_client):
    response = admin_client.get("/admin/nexvpn/usagedashboard/")

    assert response.status_code == 200
    assert "Использование туннелей" in response.content.decode()


def test_vanished_panel_user_is_queued_for_recreation():
    """Удалили в панели руками — подписка должна сама пересоздаться.

    Без этого она навсегда остаётся «синхронизированной», а `sync_panel`
    перебирает только несинхронизированные — человек молча теряет доступ.
    """
    subscription = SubscriptionFactory(panel_user_id=7, panel_status=PanelSyncStatusEnum.SYNCED)

    telemetry.take_snapshot(FakePanel([panel_user(999, used=1)]))

    subscription.refresh_from_db()
    assert subscription.panel_status == PanelSyncStatusEnum.PENDING
    assert subscription.panel_user_id is None


def test_empty_panel_response_does_not_reset_everyone():
    """Пустой ответ — это сбой панели, а не исчезновение всех пользователей."""
    subscription = SubscriptionFactory(panel_user_id=7, panel_status=PanelSyncStatusEnum.SYNCED)

    telemetry.take_snapshot(FakePanel([]))

    subscription.refresh_from_db()
    assert subscription.panel_status == PanelSyncStatusEnum.SYNCED
    assert subscription.panel_user_id == 7


def test_deleting_a_user_removes_them_from_the_panel(monkeypatch):
    """Иначе доступ у человека остаётся, а управлять им уже нечем."""
    deleted = []
    monkeypatch.setattr(
        "nexvpn.signals.RemnawaveClient",
        lambda *a, **kw: type("Fake", (), {"delete_user": lambda self, uid: deleted.append(uid)})(),
    )
    subscription = SubscriptionFactory(panel_user_id=7)

    subscription.user.delete()

    assert deleted == [7]


def test_panel_failure_does_not_block_deletion(monkeypatch):
    """Лежащая панель не должна мешать удалить человека у себя."""

    def explode(self, uid):
        raise RemnawaveError("панель недоступна")

    monkeypatch.setattr(
        "nexvpn.signals.RemnawaveClient",
        lambda *a, **kw: type("Fake", (), {"delete_user": explode})(),
    )
    subscription = SubscriptionFactory(panel_user_id=7)

    subscription.user.delete()

    assert not NexUser.objects.filter(pk=subscription.user_id).exists()


def test_admin_bulk_delete_also_reaches_the_panel(admin_client, monkeypatch):
    """В админке удаление идёт через collector — сигнал должен сработать и там."""
    deleted = []
    monkeypatch.setattr(
        "nexvpn.signals.RemnawaveClient",
        lambda *a, **kw: type("Fake", (), {"delete_user": lambda self, uid: deleted.append(uid)})(),
    )
    subscription = SubscriptionFactory(panel_user_id=7)

    admin_client.post(
        "/admin/nexvpn/nexuser/",
        {
            "action": "delete_selected",
            "_selected_action": [str(subscription.user_id)],
            "post": "yes",
        },
    )

    assert not NexUser.objects.filter(pk=subscription.user_id).exists()
    assert deleted == [7]
