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
