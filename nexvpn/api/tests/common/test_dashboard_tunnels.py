"""Раздел дашборда про туннели.

Проверяется не вёрстка, а смысл: что «людей» считается по людям, а не по
строкам, что релейные туннели не попадают в статистику операторов, и что
профиль, удалённый из подписки, всё равно виден — по нему ещё ходят те, у кого
в приложении остался старый конфиг.
"""

import datetime as dt

import pytest

from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory
from nexvpn.dashboard import periods, queries
from nexvpn.models import InboundUsageDay

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 9, 9)


@pytest.fixture(autouse=True)
def no_panel(monkeypatch):
    """Названия профилей берём фиксированные: в панель из тестов не ходим."""
    monkeypatch.setattr(queries, "profile_names", lambda: {
        ("de1-ovh", "VLESS-REALITY", False): "🇩🇪 Германия | Быстрый",
        ("eu1-ovh", "Hysteria2-Obfs", True): "🇫🇷 Франция",
    })


@pytest.fixture
def plan():
    return PlanFactory(device_limit=3, price_month=400)


def usage(plan, *, panel_id, node="de1-ovh", tag="VLESS-REALITY", relay=False,
          asn=0, operator="", network="unknown", connections=10, date=TODAY):
    subscription = SubscriptionFactory(plan=plan, panel_user_id=panel_id)
    return InboundUsageDay.objects.create(
        user=subscription.user, node_name=node, inbound_tag=tag, via_relay=relay,
        date=date, connections=connections, asn=asn, operator=operator, network=network,
    )


def period_of(days: int = 7) -> periods.Period:
    return periods.Period(TODAY - dt.timedelta(days=days - 1), TODAY, "day")


def test_people_are_counted_once_across_networks(plan):
    """Человек за сутки бывает и дома, и на мобильном — это две строки.

    В «людей» он обязан попасть один раз, иначе таблица будет врать тем
    сильнее, чем активнее человек переключается между сетями.
    """
    subscription = SubscriptionFactory(plan=plan, panel_user_id=1)
    for asn, network in ((41330, "mobile"), (12389, "fixed")):
        InboundUsageDay.objects.create(
            user=subscription.user, node_name="de1-ovh", inbound_tag="VLESS-REALITY",
            via_relay=False, date=TODAY, connections=50, asn=asn, network=network,
        )

    rows = queries.tunnels(period_of())

    assert len(rows) == 1
    assert rows[0]["people"] == 1, "один человек, а не две строки"
    assert rows[0]["connections"] == 100


def test_tunnel_gets_its_human_name(plan):
    usage(plan, panel_id=1)

    assert queries.tunnels(period_of())[0]["title"] == "🇩🇪 Германия | Быстрый"


def test_tunnel_without_a_profile_is_still_shown(plan):
    """Профиль убрали из подписки, а трафик идёт: у людей остался старый конфиг.

    Прятать такие нельзя — это и есть сигнал, что кто-то сидит на удалённом
    входе и не обновлял подписку.
    """
    usage(plan, panel_id=1, node="pl1-ovh", tag="VLESS-REALITY-VK")

    row = queries.tunnels(period_of())[0]

    assert row["known"] is False
    assert "VLESS-REALITY-VK" in row["title"] and "pl1-ovh" in row["title"]


def test_relay_rows_stay_out_of_operator_stats(plan):
    """У релейных виден адрес Москвы. Считать его за оператора человека нельзя."""
    usage(plan, panel_id=1, node="eu1-ovh", tag="Hysteria2-Obfs", relay=True, connections=999)
    usage(plan, panel_id=2, asn=41330, operator="T2", network="mobile", connections=5)

    rows = queries.operators(period_of())

    assert [row["operator"] for row in rows] == ["T2"]


def test_networks_split_keeps_the_invisible_group(plan):
    usage(plan, panel_id=1, asn=41330, operator="T2", network="mobile")
    usage(plan, panel_id=2, asn=12389, operator="RT", network="fixed")
    usage(plan, panel_id=3, node="eu1-ovh", tag="Hysteria2-Obfs", relay=True)

    rows = {row["kind"]: row["people"] for row in queries.networks(period_of())}

    assert rows == {"mobile": 1, "fixed": 1, "unknown": 1}


def test_series_has_a_line_per_tunnel(plan):
    usage(plan, panel_id=1, connections=10, date=TODAY - dt.timedelta(days=1))
    usage(plan, panel_id=2, node="eu1-ovh", tag="Hysteria2-Obfs", relay=True, connections=7)

    data = queries.tunnel_series(period_of(3), metric="people")

    assert len(data["series"]) == 2
    assert len(data["labels"]) == 3
    assert all(len(line["values"]) == 3 for line in data["series"])
    assert sum(data["series"][0]["values"]) >= 1


def test_period_outside_the_data_is_empty(plan):
    usage(plan, panel_id=1)

    old = periods.Period(dt.date(2026, 1, 1), dt.date(2026, 1, 7), "day")

    assert queries.tunnels(old) == []
    assert queries.operators(old) == []
    assert all(row["people"] == 0 for row in queries.networks(old))


# --- статистика с релея ---


def relay_row(**kwargs):
    from nexvpn.models import RelayNetworkDay

    defaults = {"date": TODAY, "node_name": "eu1-ovh", "inbound_tag": "Hysteria2-Obfs",
                "asn": 8359, "operator": "MTS", "network": "mobile",
                "connections": 100, "clients": 7}
    return RelayNetworkDay.objects.create(**{**defaults, **kwargs})


def test_relay_rows_are_stored_by_port():
    from nexvpn import telemetry
    from nexvpn.models import RelayNetworkDay

    result = telemetry.record_relay_networks([
        {"port": 9494, "date": "2026-09-09", "asn": 8359, "operator": "MTS",
         "connections": 812, "clients": 14},
    ])

    assert result.stored == 1
    row = RelayNetworkDay.objects.get()
    assert (row.node_name, row.inbound_tag) == ("eu1-ovh", "Hysteria2-Obfs")
    assert row.network == "mobile" and row.clients == 14


def test_unknown_relay_port_is_skipped_not_guessed():
    """На релее появился новый проброс — молча приписать его чужому туннелю
    хуже, чем потерять: цифры разъедутся, и никто не заметит."""
    from nexvpn import telemetry
    from nexvpn.models import RelayNetworkDay

    result = telemetry.record_relay_networks([
        {"port": 7777, "date": "2026-09-09", "asn": 8359, "connections": 5, "clients": 1},
    ])

    assert result.stored == 0 and result.unknown_users == 1
    assert not RelayNetworkDay.objects.exists()


def test_operators_keep_people_and_addresses_apart(plan):
    """Люди с прямых туннелей и адреса с релея не складываются в одну цифру."""
    usage(plan, panel_id=1, asn=8359, operator="MTS", network="mobile", connections=40)
    relay_row(asn=8359, operator="MTS", clients=7, connections=100)

    row = next(r for r in queries.operators(period_of()) if r["asn"] == 8359)

    assert row["people"] == 1, "людей знаем только по прямым"
    assert row["relay_clients"] == 7, "с релея — адреса, а не люди"
    assert row["connections"] == 140


def test_operator_seen_only_through_the_relay_is_still_listed(plan):
    relay_row(asn=41330, operator="T2", network="mobile", clients=3)

    rows = {row["asn"]: row for row in queries.operators(period_of())}

    assert rows[41330]["people"] == 0
    assert rows[41330]["relay_clients"] == 3


def test_relay_tunnel_gets_its_network_split(plan):
    """До релея у релейных туннелей в колонке «моб./дом.» было «не видно»."""
    usage(plan, panel_id=1, node="eu1-ovh", tag="Hysteria2-Obfs", relay=True, connections=99)
    relay_row(network="mobile", clients=6)
    relay_row(asn=12389, operator="RT", network="fixed", clients=2)

    row = next(r for r in queries.tunnels(period_of()) if r["via_relay"])

    assert row["network_in_clients"] is True, "цифры в адресах, и это надо подписать"
    assert (row["mobile"], row["fixed"]) == (6, 2)


# --- сводная «оператор × туннель» ---


def test_matrix_crosses_operators_with_tunnels(plan):
    """То, ради чего всё затевалось: видно, кто с какой сети куда ходит."""
    usage(plan, panel_id=1, asn=41330, operator="T2", network="mobile",
          node="de1-ovh", tag="VLESS-REALITY", connections=100)
    usage(plan, panel_id=2, asn=12389, operator="RT", network="fixed",
          node="de1-ovh", tag="VLESS-REALITY", connections=50)
    usage(plan, panel_id=3, asn=12389, operator="RT", network="fixed",
          node="pl1-ovh", tag="VLESS-GRPC", connections=10)

    matrix = queries.operator_tunnel_matrix(period_of())

    assert [row["operator"] for row in matrix["rows"]] == ["RT", "T2"], "сверху тот, у кого людей больше"
    columns = {tunnel["title"]: index for index, tunnel in enumerate(matrix["tunnels"])}
    rt = next(row for row in matrix["rows"] if row["operator"] == "RT")
    t2 = next(row for row in matrix["rows"] if row["operator"] == "T2")

    germany = columns["🇩🇪 Германия | Быстрый"]
    assert rt["cells"][germany] == 1 and t2["cells"][germany] == 1


def test_empty_cell_means_nobody_from_that_network(plan):
    """Пустая клетка — главный сигнал таблицы: на этой сети туннелем не ходят."""
    usage(plan, panel_id=1, asn=41330, operator="T2", network="mobile",
          node="de1-ovh", tag="VLESS-REALITY")
    usage(plan, panel_id=2, asn=12389, operator="RT", network="fixed",
          node="pl1-ovh", tag="VLESS-GRPC")

    matrix = queries.operator_tunnel_matrix(period_of())
    columns = {tunnel["title"]: index for index, tunnel in enumerate(matrix["tunnels"])}
    t2 = next(row for row in matrix["rows"] if row["operator"] == "T2")

    assert t2["cells"][columns["🇩🇪 Германия | Быстрый"]] == 1
    assert t2["cells"][columns["VLESS-GRPC @ pl1-ovh"]] == 0, "на Tele2 сюда никто не заходил"


def test_matrix_counts_people_once_per_cell(plan):
    """Человек, сходивший на туннель дважды за период, — всё равно один."""
    subscription = SubscriptionFactory(plan=plan, panel_user_id=1)
    for day in (TODAY, TODAY - dt.timedelta(days=1)):
        InboundUsageDay.objects.create(
            user=subscription.user, node_name="de1-ovh", inbound_tag="VLESS-REALITY",
            via_relay=False, date=day, connections=10, asn=41330,
            operator="T2", network="mobile",
        )

    matrix = queries.operator_tunnel_matrix(period_of())

    assert matrix["rows"][0]["cells"][0] == 1


def test_relay_tunnels_stay_out_of_the_matrix(plan):
    """У релейных сеть не определяется — им в этой таблице места нет."""
    usage(plan, panel_id=1, node="eu1-ovh", tag="Hysteria2-Obfs", relay=True, connections=999)
    usage(plan, panel_id=2, asn=41330, operator="T2", network="mobile")

    matrix = queries.operator_tunnel_matrix(period_of())

    assert all("Франция" not in tunnel["title"] for tunnel in matrix["tunnels"])
    assert len(matrix["tunnels"]) == 1


# --- ссылки на человека из дашборда ---


def test_user_row_links_to_the_admin(plan):
    subscription = SubscriptionFactory(plan=plan, panel_user_id=668, panel_short_uuid="CmkBrZMf")

    row = queries._user_row(subscription.user)

    assert row["admin_url"] == f"/admin/nexvpn/nexuser/{subscription.user_id}/change/"


# --- устройства в карточке ---


def test_card_shows_devices_from_the_panel(plan, monkeypatch):
    subscription = SubscriptionFactory(plan=plan, panel_user_id=450)
    monkeypatch.setattr(queries.panel_sync, "list_devices", lambda s: [
        {"hwid": "7102b1a5-ff0a-471d", "platform": "Windows", "osVersion": "10_10.0.19045",
         "deviceModel": "DESKTOP-675SBH8", "userAgent": "Happ/3.3.6/Windows/26",
         "createdAt": "2026-08-12T16:25:09.428Z", "updatedAt": "2026-09-09T14:35:38.646Z"},
        {"hwid": "aaaa", "platform": "iOS", "deviceModel": "iPhone 15",
         "createdAt": "2026-09-01T10:00:00.000Z", "updatedAt": "2026-09-09T20:00:00.000Z"},
    ])

    card = queries.user_card(subscription.user_id)

    assert card["devices"]["ok"] is True
    titles = [d["title"] for d in card["devices"]["items"]]
    assert titles == ["iPhone 15", "DESKTOP-675SBH8"], "свежие сверху"
    first = card["devices"]["items"][1]
    assert first["app"] == "Happ" and first["platform"] == "Windows"
    assert first["last_seen"] == "2026-09-09T14:35:38.646Z"


def test_panel_silence_is_not_the_same_as_no_devices(plan, monkeypatch):
    """«Устройств нет» и «не смогли спросить» — разные вещи, и путать их нельзя."""
    subscription = SubscriptionFactory(plan=plan, panel_user_id=450)

    def boom(s):
        raise RuntimeError("панель недоступна")

    monkeypatch.setattr(queries.panel_sync, "list_devices", boom)

    card = queries.user_card(subscription.user_id)

    assert card["devices"] == {"ok": False, "items": []}


def test_no_panel_user_means_no_devices(plan, monkeypatch):
    subscription = SubscriptionFactory(plan=plan, panel_user_id=None)
    monkeypatch.setattr(queries.panel_sync, "list_devices",
                        lambda s: pytest.fail("в панель ходить незачем"))

    assert queries.user_card(subscription.user_id)["devices"] == {"ok": True, "items": []}
