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
