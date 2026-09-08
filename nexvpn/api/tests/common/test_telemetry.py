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


def test_scheduled_sync_refuses_to_run_from_a_dev_machine(settings, monkeypatch):
    """Локальный beat однажды начал переписывать боевой панели сроки из дев-базы.

    Команду `sync_panel` мы защитили сразу, а периодическую задачу — нет, хотя
    по расписанию крутится именно она.
    """
    from nexvpn import tasks

    settings.DEBUG = True
    called = []
    monkeypatch.setattr(
        "nexvpn.subscription.panel_sync.sync_pending",
        lambda *a, **kw: called.append(True) or (0, 0),
    )

    assert tasks.sync_panel() == {"skipped": True}
    assert not called


# --- названия профилей ---


class FakeHostsPanel:
    """Панель, отвечающая на три вызова, которые нужны profile_names."""

    def __init__(self, hosts):
        self.hosts = hosts

    def list_nodes(self):
        return [{"name": "de1-ovh", "address": "de1.pineferry.com"},
                {"name": "eu1-ovh", "address": "eu1.pineferry.com"}]

    def list_inbounds(self):
        return [{"uuid": "aaaa", "tag": "VLESS-REALITY"}, {"uuid": "bbbb", "tag": "VLESS-GRPC"}]

    def list_hosts(self):
        return self.hosts


def test_profile_names_read_the_nested_inbound(settings):
    """Панель переехала с `inboundUuid` на `inbound.configProfileInboundUuid`.

    Пока код читал плоское поле, сопоставление возвращало пустой словарь — и
    статистика молча показывала теги вместо названий профилей.
    """
    settings.RELAY_NODE_ADDRESS = "ru1.pineferry.com"
    panel = FakeHostsPanel([
        {"remark": "🇩🇪 Быстрый — Германия", "address": "de1.pineferry.com",
         "inbound": {"configProfileInboundUuid": "aaaa"}},
    ])

    names = telemetry.profile_names(panel)

    assert names[("de1-ovh", "VLESS-REALITY", False)] == "🇩🇪 Быстрый — Германия"


def test_profile_names_still_read_the_old_flat_field(settings):
    """Откат панели не должен снова обнулять названия."""
    settings.RELAY_NODE_ADDRESS = "ru1.pineferry.com"
    panel = FakeHostsPanel([
        {"remark": "Франция 3", "address": "eu1.pineferry.com", "inboundUuid": "bbbb"},
    ])

    names = telemetry.profile_names(panel)

    assert names[("eu1-ovh", "VLESS-GRPC", False)] == "Франция 3"


def test_relay_host_is_attributed_to_the_node_behind_it(settings):
    """Хост смотрит на релей, а инбаунд лежит на ноде — это профиль «через релей»."""
    settings.RELAY_NODE_ADDRESS = "ru1.pineferry.com"
    panel = FakeHostsPanel([
        {"remark": "🇷🇺 Альтернативный", "address": "ru1.pineferry.com",
         "inbound": {"configProfileInboundUuid": "bbbb"}},
    ])

    names = telemetry.profile_names(panel)

    assert names[("eu1-ovh", "VLESS-GRPC", True)] == "🇷🇺 Альтернативный"


def test_unmapped_hosts_are_shouted_about(settings, monkeypatch):
    """Молчаливая пустота — та самая поломка, которую не заметили неделями.

    Перехватываем логгер напрямую, а не через caplog: у логгера `nexvpn` в
    настройках стоит `propagate: False`, и до корневого обработчика, который
    слушает caplog, запись не доходит.
    """
    settings.RELAY_NODE_ADDRESS = "ru1.pineferry.com"
    panel = FakeHostsPanel([
        {"remark": "Непонятный", "address": "de1.pineferry.com", "inbound": {"whatIsThis": "zzz"}},
    ])
    shouted: list[str] = []
    monkeypatch.setattr(telemetry.logger, "error", lambda msg, *a, **kw: shouted.append(str(msg)))

    names = telemetry.profile_names(panel)

    assert names == {}
    assert any("не сопоставился" in message for message in shouted)


# --- статистика по туннелям и сетям ---


def make_subscription(panel_user_id: int):
    return SubscriptionFactory(panel_user_id=panel_user_id, panel_short_uuid=f"s{panel_user_id}")


def test_inbound_rows_are_stored_with_the_network():
    from nexvpn.models import InboundUsageDay

    subscription = make_subscription(413)

    result = telemetry.record_inbound_usage("de1-ovh", [{
        "panel_user_id": 413, "inbound_tag": "VLESS-REALITY", "via_relay": False,
        "date": "2026-09-09", "connections": 128, "asn": 41330,
        "operator": "T2-NOVOSIBIRSK-AS T2 Russia Network",
    }])

    assert result.stored == 1
    row = InboundUsageDay.objects.get()
    assert row.user_id == subscription.user_id
    assert row.asn == 41330
    assert row.network == InboundUsageDay.Network.MOBILE, "Tele2 — мобильная сеть"


def test_the_same_person_on_two_networks_is_two_rows():
    """За сутки человек бывает и дома, и на мобильном. Это разные строки —
    иначе нельзя ответить, каким туннелем не пользуются на Tele2."""
    from nexvpn.models import InboundUsageDay

    make_subscription(413)
    base = {"panel_user_id": 413, "inbound_tag": "VLESS-REALITY", "via_relay": False,
            "date": "2026-09-09"}

    telemetry.record_inbound_usage("de1-ovh", [
        {**base, "connections": 100, "asn": 41330, "operator": "T2 Russia Network"},
        {**base, "connections": 20, "asn": 12389, "operator": "ROSTELECOM-AS"},
    ])

    rows = {row.asn: row for row in InboundUsageDay.objects.all()}
    assert set(rows) == {41330, 12389}
    assert rows[41330].network == InboundUsageDay.Network.MOBILE
    assert rows[12389].network == InboundUsageDay.Network.FIXED


def test_repeated_delivery_does_not_double_the_count():
    """Значения абсолютные: повтор перезаписывает тем же числом."""
    from nexvpn.models import InboundUsageDay

    make_subscription(413)
    row = {"panel_user_id": 413, "inbound_tag": "VLESS-REALITY", "via_relay": False,
           "date": "2026-09-09", "connections": 128, "asn": 41330, "operator": "T2"}

    telemetry.record_inbound_usage("de1-ovh", [row])
    telemetry.record_inbound_usage("de1-ovh", [row])

    assert InboundUsageDay.objects.count() == 1
    assert InboundUsageDay.objects.get().connections == 128


def test_unknown_network_does_not_break_the_row():
    from nexvpn.models import InboundUsageDay

    make_subscription(413)

    telemetry.record_inbound_usage("de1-ovh", [{
        "panel_user_id": 413, "inbound_tag": "Hysteria2-Obfs", "via_relay": True,
        "date": "2026-09-09", "connections": 5,
    }])

    row = InboundUsageDay.objects.get()
    assert row.asn == 0 and row.operator == ""
    assert row.network == InboundUsageDay.Network.UNKNOWN


def test_network_table_beats_the_name_hint():
    """Корбина — домашний Билайн, хотя в названии оператора мобильных подсказок нет."""
    from nexvpn import networks
    from nexvpn.models import InboundUsageDay

    assert networks.classify(8402, "CORBINA-AS OJSC Vimpelcom") == InboundUsageDay.Network.FIXED
    assert networks.classify(41330, "T2-NOVOSIBIRSK-AS") == InboundUsageDay.Network.MOBILE
    # Незнакомая сеть — вывод по названию.
    assert networks.classify(999999, "SOMETHING TELE2 GSM") == InboundUsageDay.Network.MOBILE
    assert networks.classify(0, "что угодно") == InboundUsageDay.Network.UNKNOWN
