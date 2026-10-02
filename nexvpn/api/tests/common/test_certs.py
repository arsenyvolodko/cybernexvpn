"""Сроки сертификатов на нодах.

Проверка появилась после того, как сертификат `*.pineferry.com` чуть не истёк
7 ноября одновременно на всех нодах: продлевался он только на одной, а узнать
об этом было негде. Поэтому здесь важнее всего не «красиво показать», а не
соврать в трёх разных случаях «ничего»: не проверяли, нечего проверять, не
дозвонились.
"""

import datetime as dt

import pytest
from django.core.cache import cache
from django.utils import timezone

from nexvpn.dashboard import certs


@pytest.fixture(autouse=True)
def clean_cache():
    cache.delete(certs.CACHE_KEY)
    yield
    cache.delete(certs.CACHE_KEY)


def fake_probe(answers):
    """Подменяет рукопожатие: ключ — (хост, порт), значение — что «ответило»."""
    def inner(host, port):
        return answers.get((host, port))
    return inner


def days_from_now(n):
    return (timezone.localdate() + dt.timedelta(days=n)).isoformat()


def test_nothing_in_cache_means_not_checked_yet():
    assert certs.known() == {}


def test_earliest_name_decides_for_a_node_with_two(monkeypatch):
    """У ru2 два имени, и туннель ломается по первому истёкшему, не по среднему."""
    monkeypatch.setattr(certs, "probe", fake_probe({
        ("ru2.pineferry.com", 7443): {"host": "ru2.pineferry.com", "port": 7443,
                                      "name": "ru2.pineferry.com", "until": days_from_now(40)},
        ("ru2b.pineferry.com", 8080): {"host": "ru2b.pineferry.com", "port": 8080,
                                       "name": "ru2b.pineferry.com", "until": days_from_now(9)},
    }))
    monkeypatch.setattr(certs, "TLS_ENDPOINTS", {
        "ru2-eurobyte": [("ru2.pineferry.com", 7443), ("ru2b.pineferry.com", 8080)]})

    found = certs.refresh()["ru2-eurobyte"]

    assert found["days"] == 9
    assert found["host"] == "ru2b.pineferry.com"
    assert len(found["endpoints"]) == 2


def test_silent_node_is_told_apart_from_a_fresh_one(monkeypatch):
    """«Не дозвонились» — это повод идти смотреть, а не прочерк в таблице."""
    monkeypatch.setattr(certs, "probe", fake_probe({}))
    monkeypatch.setattr(certs, "TLS_ENDPOINTS", {"de1-ovh": [("de1.pineferry.com", 7443)]})

    found = certs.refresh()["de1-ovh"]

    assert found["reachable"] is False
    assert "until" not in found


def test_result_is_cached_for_the_dashboard(monkeypatch):
    """Страница в сеть не ходит: шесть нод по пять секунд её бы заморозили."""
    monkeypatch.setattr(certs, "probe", fake_probe({
        ("de1.pineferry.com", 7443): {"host": "de1.pineferry.com", "port": 7443,
                                      "name": "de1.pineferry.com", "until": days_from_now(60)}}))
    monkeypatch.setattr(certs, "TLS_ENDPOINTS", {"de1-ovh": [("de1.pineferry.com", 7443)]})

    certs.refresh()

    assert certs.known()["de1-ovh"]["days"] == 60


def test_task_reports_nodes_whose_renewal_did_not_happen(monkeypatch):
    """Let's Encrypt продлевает за 30 дней. Три недели — уже сломанное продление.

    Проверяем возвращаемое значение, а не журнал: журнал здесь ведёт loguru, и
    завязка на его формат сломается при первой же смене строки.
    """
    from nexvpn.tasks import check_node_certificates

    monkeypatch.setattr(certs, "probe", fake_probe({
        ("de1.pineferry.com", 7443): {"host": "de1.pineferry.com", "port": 7443,
                                      "name": "de1.pineferry.com", "until": days_from_now(5)},
        ("pl1.pineferry.com", 7443): {"host": "pl1.pineferry.com", "port": 7443,
                                      "name": "pl1.pineferry.com", "until": days_from_now(70)},
    }))
    monkeypatch.setattr(certs, "TLS_ENDPOINTS", {
        "de1-ovh": [("de1.pineferry.com", 7443)],
        "pl1-ovh": [("pl1.pineferry.com", 7443)],
        "de2-ghostnet": [("de2.pineferry.com", 7443)],
    })

    result = check_node_certificates()

    assert result["tight"] == {"de1-ovh": 5}, "на исходе только de1"
    assert result["silent"] == ["de2-ghostnet"], "de2 не ответил — это отдельный случай"
    assert result["checked"] == 3


@pytest.fixture
def panel_snapshot():
    """Минимум, который нужен блоку серверов: одна нода на связи."""
    return {"nodes": [{"name": "de1-ovh", "address": "de1.pineferry.com",
                       "isConnected": True, "usersOnline": 3,
                       "trafficUsedBytes": 0, "trafficLimitBytes": 0}],
            "profiles": [], "inbounds": [], "squads": [], "hosts": [], "templates": []}


@pytest.fixture
def server_rows():
    return [
        {"name": "de1-ovh", "address": "de1.pineferry.com", "panel_node_name": "de1-ovh",
         "role": "exit_abroad", "role_display": "Выход за рубеж", "currency": "RUB",
         "is_active": True},
        {"name": "relay-ru", "address": "185.0.0.1", "panel_node_name": "",
         "role": "relay_ru", "role_display": "Релей РФ", "currency": "RUB",
         "is_active": True},
    ]


@pytest.mark.django_db
def test_servers_block_shows_the_certificate(monkeypatch, panel_snapshot, server_rows):
    """Срок должен попадать в ту же таблицу, по которой сверяют счета."""
    from nexvpn.dashboard import infra

    cache.set(certs.CACHE_KEY, {
        "de1-ovh": {"checked": True, "reachable": True, "until": days_from_now(12),
                    "days": 12, "name": "de1.pineferry.com", "host": "de1.pineferry.com"},
    }, 60)

    block = infra.servers(panel_snapshot, server_rows)
    rows = {r["name"]: r for r in block["rows"]}

    assert rows["de1-ovh"]["cert"]["days"] == 12
    # У релея нашего TLS нет вовсе — это не ошибка, и путать с «молчит» нельзя.
    assert rows["relay-ru"]["cert"] is None
    assert block["cert_soonest"]["node"] == "de1-ovh"
    assert block["cert_alarm_days"] == certs.ALARM_DAYS


@pytest.mark.django_db
def test_dashboard_page_carries_the_certificate_column(admin_client):
    html = admin_client.get("/dash/").content.decode()

    assert "Сертификат" in html
    assert "srvCert" in html
