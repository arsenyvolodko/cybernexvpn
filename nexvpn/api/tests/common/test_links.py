"""Связки «вход → выход → протокол»: свод, топ и матрица по туннелям."""

import datetime as dt

import pytest
from django.urls import reverse

from nexvpn.dashboard import links
from nexvpn.models import LinkUsageDay

TODAY = dt.date(2026, 9, 29)
YESTERDAY = TODAY - dt.timedelta(days=1)


def make(day, entry, exit_node, protocol, **kw):
    return LinkUsageDay.objects.create(
        date=day, entry=entry, exit_node=exit_node, protocol=protocol,
        bytes_in=kw.get("bytes_in", 0), bytes_out=kw.get("bytes_out", 0),
        connections=kw.get("connections", 0), probes=kw.get("probes", 0),
        users=kw.get("users", []), probing=kw.get("probing", []),
    )


@pytest.mark.django_db
def test_people_over_period_are_merged_not_summed():
    """Один и тот же человек два дня подряд — это один человек, а не два."""
    make(YESTERDAY, "atlex", "de2-ghostnet", "grpc", users=["1", "2"], bytes_out=100)
    make(TODAY, "atlex", "de2-ghostnet", "grpc", users=["2", "3"], bytes_out=50)

    top = links.top_links(YESTERDAY, TODAY)

    assert len(top) == 1
    assert top[0]["users"] == 3
    assert top[0]["bytes"] == 150


@pytest.mark.django_db
def test_probing_only_users_are_not_counted_as_real():
    """Кто только щупал плечо автовыбором — не пользователь этой связки."""
    make(TODAY, "timeweb", "cs1-cherryservers", "reality",
         users=["1"], probing=["1", "7", "8"], probes=300)

    row = links.top_links(TODAY, TODAY)[0]

    assert row["users"] == 1
    assert row["probing"] == 2, "щупавший, который потом качал, считается качавшим"


@pytest.mark.django_db
def test_top_is_sorted_by_traffic_and_limited():
    for i in range(5):
        make(TODAY, "atlex", f"node-{i}", "reality", bytes_out=i * 1000)

    top = links.top_links(TODAY, TODAY, limit=3)

    assert len(top) == 3
    assert [r["bytes"] for r in top] == [4000, 3000, 2000]


@pytest.mark.django_db
def test_matrix_skips_auto_tunnels_and_fills_cells(monkeypatch):
    """«Авто» в матрице не место: его трафик уже посчитан на тех же плечах."""
    make(TODAY, "atlex", "de2-ghostnet", "grpc", bytes_out=700, users=["1", "2"])
    snapshot = {"hosts": [], "nodes": [], "inbounds": [], "templates": [], "squads": []}
    blueprint = {
        "tunnels": [
            {"title": "Авто", "auto": True, "position": 0, "legs": [
                {"address": "185.71.198.238", "inbound": "VLESS-GRPC",
                 "exit": {"node": "de2-ghostnet"}}]},
            {"title": "Обход 8", "auto": False, "position": 1, "legs": [
                {"address": "185.71.198.238", "inbound": "VLESS-GRPC",
                 "exit": {"node": "de2-ghostnet"}}]},
        ]
    }
    monkeypatch.setattr(links.infra, "blueprint", lambda *a, **k: blueprint)

    matrix = links.tunnel_matrix(TODAY, TODAY, snapshot=snapshot)

    titles = [r["title"] for r in matrix["rows"]]
    assert titles == ["Обход 8"], "автоподбор не должен попадать в таблицу"
    assert matrix["rows"][0]["cells"]["grpc"] == {"bytes": 700, "users": 2}
    assert [c["key"] for c in matrix["columns"]] == ["grpc"]


@pytest.mark.django_db
def test_endpoint_answers_staff(admin_client, monkeypatch):
    make(TODAY, "atlex", "de2-ghostnet", "reality", bytes_out=10, users=["1"])
    monkeypatch.setattr(links.infra, "snapshot", lambda *a, **k: {"hosts": [], "nodes": []})
    monkeypatch.setattr(links.infra, "blueprint", lambda *a, **k: {"tunnels": []})

    response = admin_client.get(reverse("dashboard") + "api/links/")

    assert response.status_code == 200
    body = response.json()
    assert "matrix" in body and "top" in body
    assert body["top"][0]["users"] == 1


@pytest.mark.django_db
def test_dashboard_page_has_new_blocks_and_no_old_one(admin_client):
    """Старый блок «Туннели» убран, два новых на месте, мёртвых ссылок нет."""
    page = admin_client.get(reverse("dashboard")).content.decode()

    assert "Туннели × протоколы" in page
    assert "Каналы подключения" in page
    assert "tunnelChart" not in page, "разметка удалена, ссылки в скриптах остаться не должны"
    assert "drawMultiChart" not in page
    # порядок блоков: платежи, люди, активность, когорты
    assert page.index("<h2>Платежи</h2>") < page.index("<h2>Пользователи</h2>")
    assert page.index("<h2>Пользователи</h2>") < page.index("Активность по дням")
    assert page.index("Активность по дням") < page.index("Когорты по неделям регистрации")


# --- среднее за период и свой период у блока ---


@pytest.mark.django_db
def test_average_divides_by_days_with_data_not_by_period_length():
    """Сбор связок моложе остальных данных, и это главная ловушка среднего.

    До запуска сбора у нас не «ноль трафика», а «неизвестно». Поделив месячный
    итог на тридцать, мы показали бы среднее втрое ниже правды — и решения по
    трафику принимались бы по заниженной цифре.
    """
    make(YESTERDAY, "atlex", "de2-ghostnet", "grpc", bytes_out=300)
    make(TODAY, "atlex", "de2-ghostnet", "grpc", bytes_out=100)
    month_ago = TODAY - dt.timedelta(days=29)

    assert links.observed_days(month_ago, TODAY) == 2
    row = links.top_links(month_ago, TODAY)[0]
    # 400 байт за двое наблюдаемых суток, а не за тридцать календарных.
    assert row["per_day"] == 200


@pytest.mark.django_db
def test_today_counts_as_a_fraction_of_a_day():
    """Целыми сутками сегодняшний день занижал бы среднее: в полдень — вдвое."""
    from django.utils import timezone

    today = timezone.localdate()
    make(today, "atlex", "de2-ghostnet", "grpc", bytes_out=100)

    days = links.observed_days(today, today)

    assert 0 < days < 1


@pytest.mark.django_db
def test_average_is_empty_when_there_are_no_observations():
    assert links.observed_days(YESTERDAY, TODAY) == 0
    assert links.top_links(YESTERDAY, TODAY) == []


@pytest.mark.django_db
def test_block_period_is_independent_of_the_header(admin_client):
    """У блока свой период: смотреть связки тем же месяцем, что платежи, незачем."""
    from django.utils import timezone

    today = timezone.localdate()
    long_ago = today - dt.timedelta(days=20)
    make(long_ago, "atlex", "de2-ghostnet", "grpc", bytes_out=999)
    make(today, "atlex", "de2-ghostnet", "grpc", bytes_out=100)

    week = admin_client.get("/dash/api/links/", {"days": 7}).json()
    month = admin_client.get("/dash/api/links/", {"days": 30}).json()

    assert week["top"][0]["bytes"] == 100, "за неделю старая строка попадать не должна"
    assert month["top"][0]["bytes"] == 1099
    assert week["period"]["to"] == today.isoformat()
    assert week["period"]["from"] == (today - dt.timedelta(days=6)).isoformat()


@pytest.mark.django_db
def test_matrix_carries_the_observed_days(admin_client):
    make(TODAY, "atlex", "de2-ghostnet", "grpc", bytes_out=100)

    body = admin_client.get("/dash/api/links/").json()

    assert "observed_days" in body["matrix"]
    assert body["period"]["observed_days"] == body["matrix"]["observed_days"]


@pytest.mark.django_db
def test_dashboard_page_carries_the_period_picker(admin_client):
    html = admin_client.get("/dash/").content.decode()

    assert 'id="linkDays"' in html and 'id="linkDays2"' in html
    assert "В среднем" in html
    assert "fmtRate" in html


# --- туннели через цепочку ru2 ---


@pytest.mark.django_db
def test_chain_tunnel_finds_its_traffic_despite_a_different_exit(monkeypatch):
    """Все «Обходы глушилок» через ru2 показывали нули, и это была ошибка связки.

    ru2 обрывает клиента на себе и идёт дальше на зарубежный выход, поэтому в
    данных у неё стоит настоящий выход цепочки (cs1), а в панели хост туннеля
    привязан к самой ru2. Ключ поиска не совпадал, и туннель выглядел мёртвым
    при живом трафике — хуже всего то, что выглядел правдоподобно.
    """
    make(TODAY, "eurobyte_a", "cs1-cherryservers", "grpc", bytes_out=500, users=["1", "2"])
    snapshot = {"hosts": [], "nodes": [], "inbounds": [], "templates": [], "squads": []}
    blueprint = {"tunnels": [
        {"title": "Обход глушилок 1 | Моб. интернет", "auto": False, "position": 1, "legs": [
            {"address": "ru2.pineferry.com", "inbound": "RU2-GRPC-A",
             "exit": {"node": "ru2-eurobyte"}}]},
    ]}
    monkeypatch.setattr(links.infra, "blueprint", lambda *a, **k: blueprint)

    row = links.tunnel_matrix(TODAY, TODAY, snapshot=snapshot)["rows"][0]

    assert row["bytes"] == 500
    assert row["users"] == 2
    assert row["cells"]["grpc"] == {"bytes": 500, "users": 2}


@pytest.mark.django_db
def test_chain_lookup_does_not_mix_the_two_entry_addresses(monkeypatch):
    """У ru2 два адреса, A и B, и это разные туннели в подписке.

    Искать по связке «вход + протокол» можно только потому, что вход различает
    адреса. Склей их — и трафик одного туннеля утечёт в другой.
    """
    make(TODAY, "eurobyte_a", "cs1-cherryservers", "trojan_or_vk", bytes_out=100, users=["1"])
    make(TODAY, "eurobyte_b", "de2-ghostnet", "trojan_or_vk", bytes_out=900, users=["2", "3"])
    snapshot = {"hosts": [], "nodes": [], "inbounds": [], "templates": [], "squads": []}
    blueprint = {"tunnels": [
        {"title": "Обход 2 (адрес A)", "auto": False, "position": 1, "legs": [
            {"address": "ru2.pineferry.com", "inbound": "RU2-TROJAN-A",
             "exit": {"node": "ru2-eurobyte"}}]},
        {"title": "Обход 5 (адрес B)", "auto": False, "position": 2, "legs": [
            {"address": "ru2b.pineferry.com", "inbound": "RU2-TROJAN-B",
             "exit": {"node": "ru2-eurobyte"}}]},
    ]}
    monkeypatch.setattr(links.infra, "blueprint", lambda *a, **k: blueprint)

    rows = {r["title"]: r for r in links.tunnel_matrix(TODAY, TODAY, snapshot=snapshot)["rows"]}

    assert rows["Обход 2 (адрес A)"]["bytes"] == 100
    assert rows["Обход 5 (адрес B)"]["bytes"] == 900


@pytest.mark.django_db
def test_ordinary_tunnel_still_matches_its_own_exit(monkeypatch):
    """Обычный туннель ищет строго свой выход: иначе сложит все выходы разом."""
    make(TODAY, "atlex", "de1-ovh", "grpc", bytes_out=10, users=["1"])
    make(TODAY, "atlex", "pl1-ovh", "grpc", bytes_out=20, users=["2"])
    snapshot = {"hosts": [], "nodes": [], "inbounds": [], "templates": [], "squads": []}
    blueprint = {"tunnels": [
        {"title": "Обход 14", "auto": False, "position": 1, "legs": [
            {"address": "185.71.198.238", "inbound": "VLESS-GRPC",
             "exit": {"node": "de1-ovh"}}]},
    ]}
    monkeypatch.setattr(links.infra, "blueprint", lambda *a, **k: blueprint)

    row = links.tunnel_matrix(TODAY, TODAY, snapshot=snapshot)["rows"][0]

    assert row["bytes"] == 10, "сложил оба выхода вместо одного"
