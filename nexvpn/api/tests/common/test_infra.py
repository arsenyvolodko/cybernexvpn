"""Учёт серверов и разбор устройства туннелей.

Проверяется смысл, а не вёрстка: что выход через российский релей вообще
определяется (панель его прямо не сообщает), что правила маршрутизации
превращаются в понятные фразы, и что проверки здоровья ловят ровно те поломки,
на которых мы уже обжигались — выключенный хост, пропавший из профиля выход,
несуществующий запасной и инбаунд, который никому не раздаётся.
"""

import datetime as dt
from decimal import Decimal

import pytest

from nexvpn.dashboard import infra
from nexvpn.models import Server, ServerCost

pytestmark = pytest.mark.django_db


# ─────────────────────────── фикстура панели ───────────────────────────

DEFAULT_PROFILE = "00000000-0000-0000-0000-000000000000"
CS_PROFILE = "67fb1c7b"

INB_REALITY = "inb-reality"
INB_GRPC = "inb-grpc"
INB_CS = "inb-cs"
INB_LONELY = "inb-lonely"


def inbound(uuid, tag, profile, *, network="tcp", security="reality", port=443, kind="vless"):
    return {"uuid": uuid, "tag": tag, "profileUuid": profile, "type": kind,
            "network": network, "security": security, "port": port}


def host(uuid, remark, address, port, inbound_uuid, **extra):
    row = {"uuid": uuid, "remark": remark, "address": address, "port": port,
           "isHidden": False, "isDisabled": False, "viewPosition": 0,
           "xrayJsonTemplateUuid": None,
           "inbound": {"configProfileInboundUuid": inbound_uuid}}
    row.update(extra)
    return row


def template(uuid, name, *, inject, rules=None, balancers=None, observatory=None):
    config = {
        "routing": {"domainStrategy": "AsIs", "rules": rules or [], "balancers": balancers or []},
        "remnawave": {"injectHosts": [
            {"selector": {"type": "uuids", "values": [h]}, "tagPrefix": tag, "selectFrom": "ALL"}
            for tag, h in inject
        ]},
        "policy": {"levels": {"0": {"bufferSize": 64, "connIdle": 180}}},
    }
    if observatory:
        config["burstObservatory"] = observatory
    return {"uuid": uuid, "name": name, "templateType": "XRAY_JSON", "templateJson": config}


@pytest.fixture
def snapshot():
    """Маленькая копия настоящей панели: релей, три ноды, профиль из шаблона."""
    return {
        "nodes": [
            {"name": "de1-ovh", "address": "de1.example.com", "isConnected": True,
             "usersOnline": 20, "trafficUsedBytes": 5 * infra.TB, "trafficLimitBytes": 0,
             "configProfile": {"activeConfigProfileUuid": DEFAULT_PROFILE}},
            {"name": "pl1-ovh", "address": "pl1.example.com", "isConnected": True,
             "usersOnline": 8, "trafficUsedBytes": 0, "trafficLimitBytes": 0,
             "configProfile": {"activeConfigProfileUuid": DEFAULT_PROFILE}},
            {"name": "cs1-cherry", "address": "88.0.0.1", "isConnected": False,
             "usersOnline": 0, "trafficUsedBytes": 0, "trafficLimitBytes": 0,
             "configProfile": {"activeConfigProfileUuid": CS_PROFILE}},
        ],
        "profiles": [
            {"uuid": DEFAULT_PROFILE, "name": "Default-Profile",
             "nodes": [{"name": "de1-ovh"}, {"name": "pl1-ovh"}]},
            {"uuid": CS_PROFILE, "name": "CS-Profile", "nodes": [{"name": "cs1-cherry"}]},
        ],
        "inbounds": [
            inbound(INB_REALITY, "VLESS-REALITY", DEFAULT_PROFILE),
            inbound(INB_GRPC, "VLESS-GRPC", DEFAULT_PROFILE, network="grpc", security="tls", port=7443),
            inbound(INB_CS, "CS-VLESS-REALITY", CS_PROFILE),
            inbound(INB_LONELY, "CS-GOOGLE", CS_PROFILE, port=2096),
        ],
        "squads": [{"uuid": "sq", "inbounds": [INB_REALITY, INB_GRPC, INB_CS]}],
        "hosts": [],
        "templates": [],
    }


@pytest.fixture
def servers_rows():
    return [
        {"name": "de1-ovh", "address": "de1.example.com", "panel_node_name": "de1-ovh",
         "country": "Германия", "role": "exit_abroad"},
        {"name": "pl1-ovh", "address": "pl1.example.com", "panel_node_name": "pl1-ovh",
         "country": "Польша", "role": "exit_abroad"},
        {"name": "cs1-cherry", "address": "88.0.0.1", "panel_node_name": "cs1-cherry",
         "country": "Нидерланды", "role": "exit_abroad"},
        {"name": "relay-ru", "address": "185.0.0.1", "panel_node_name": "",
         "country": "Россия", "role": "relay_ru"},
    ]


def only(blueprint, title):
    return next(t for t in blueprint["tunnels"] if t["title"] == title)


# ─────────────────────────── откуда и куда ───────────────────────────


def test_direct_host_is_entry_and_exit_at_once(snapshot, servers_rows):
    """Адрес хоста совпал с адресом ноды — значит заходим прямо на неё."""
    snapshot["hosts"] = [host("h1", "🇩🇪 Германия", "de1.example.com", 443, INB_REALITY)]
    leg = only(infra.blueprint(snapshot, servers_rows), "🇩🇪 Германия")["legs"][0]

    assert leg["direct"] is True
    assert leg["entry"]["server"] == "de1-ovh"
    assert leg["exit"]["server"] == "de1-ovh"
    assert leg["exit"]["source"] == "адрес"


def test_relay_exit_is_resolved_by_name_when_profile_has_many_nodes(snapshot, servers_rows):
    """Вход в России, выход за рубежом — панель об этом молчит.

    Профиль крутится на двух нодах, поэтому по нему одному не понять; спасает
    наше же соглашение об именовании «релей → нода».
    """
    snapshot["hosts"] = [host("h1", "🔒 relay-iot → pl1 REALITY", "185.0.0.1", 3443, INB_REALITY)]
    leg = only(infra.blueprint(snapshot, servers_rows), "🔒 relay-iot → pl1 REALITY")["legs"][0]

    assert leg["direct"] is False
    assert leg["entry"]["server"] == "relay-ru"
    assert leg["entry"]["country"] == "Россия"
    assert leg["exit"]["server"] == "pl1-ovh"
    assert leg["exit"]["source"] == "название"


def test_relay_exit_is_unambiguous_when_profile_has_one_node(snapshot, servers_rows):
    """Профиль живёт на одной ноде — гадать не о чем, даже без подписи."""
    snapshot["hosts"] = [host("h1", "через релей", "185.0.0.1", 10443, INB_CS)]
    leg = only(infra.blueprint(snapshot, servers_rows), "через релей")["legs"][0]

    assert leg["exit"]["server"] == "cs1-cherry"
    assert leg["exit"]["source"] == "профиль"


def test_unresolvable_relay_is_a_note_not_a_breakage(snapshot, servers_rows):
    """«Не знаю, куда ведёт» — пробел в данных, а не поломка.

    Иначе бы вечно горело красным на исправно работающих туннелях.
    """
    snapshot["hosts"] = [host("h1", "🇫🇷 Франция", "185.0.0.1", 443, INB_GRPC)]
    tunnel = only(infra.blueprint(snapshot, servers_rows), "🇫🇷 Франция")

    assert tunnel["legs"][0]["exit"]["server"] == ""
    assert tunnel["problems"] == []
    assert any("не понять" in note for note in tunnel["notes"])


def test_server_is_found_by_node_address_too(snapshot, servers_rows):
    """В базе адрес записан IP, в панели — доменом. Должно сойтись всё равно."""
    servers_rows[0]["address"] = "1.2.3.4"
    snapshot["hosts"] = [host("h1", "🇩🇪 Германия", "de1.example.com", 443, INB_REALITY)]
    leg = only(infra.blueprint(snapshot, servers_rows), "🇩🇪 Германия")["legs"][0]

    assert leg["entry"]["server"] == "de1-ovh"


# ─────────────────────────── транспорт ───────────────────────────


def test_grpc_and_hysteria_are_marked_as_multiplexed():
    """Пометка не украшение: на лавине переподключений выживают только они."""
    assert infra.is_multiplexed({"network": "grpc"}) is True
    assert infra.is_multiplexed({"network": "hysteria"}) is True
    assert infra.is_multiplexed({"network": "tcp"}) is False


def test_transport_reads_like_a_sentence():
    assert infra.transport_label({"type": "vless", "network": "grpc", "security": "tls"}) == "VLESS · gRPC · TLS"
    assert infra.transport_label({"type": "hysteria", "network": "hysteria"}) == "Hysteria2 · QUIC"
    assert infra.transport_short({"type": "vless", "network": "tcp", "security": "reality"}) == "REALITY"


# ─────────────────────────── правила словами ───────────────────────────


def test_russian_zone_is_recognised_as_runet():
    assert infra.domain_theme(["regexp:\\.ru$", "domain:vk.com"]) == "рунет"


def test_several_themes_in_one_rule_are_all_named():
    """У нас в одном правиле лежат и тикток, и Gemini — назвать надо оба."""
    theme = infra.domain_theme(["domain:tiktok.com", "domain:byteoversea.com", "domain:gemini.google.com"])
    assert theme == "тикток и Gemini"


def test_routing_rules_turn_into_readable_lines(snapshot, servers_rows):
    snapshot["hosts"] = [
        host("ru", "🔒 ru-exit", "de1.example.com", 443, INB_REALITY, isHidden=True),
        host("main", "Умный", "de1.example.com", 443, INB_REALITY, xrayJsonTemplateUuid="t1"),
    ]
    snapshot["templates"] = [template("t1", "Split", inject=[("ruv", "ru")], rules=[
        {"type": "field", "domain": ["regexp:\\.ru$", "domain:vk.com"], "outboundTag": "direct"},
        {"type": "field", "network": "udp", "port": "443", "outboundTag": "block"},
        {"type": "field", "domain": ["domain:youtube.com", "domain:googlevideo.com"], "outboundTag": "ruv"},
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "direct"},
    ])]
    rules = only(infra.blueprint(snapshot, servers_rows), "Умный")["routing"]

    assert rules[0]["what"].startswith("рунет") and rules[0]["where"] == "мимо VPN"
    assert rules[1]["where"] == "блокируется" and "QUIC" in rules[1]["what"]
    assert rules[2]["what"].startswith("ютуб") and rules[2]["where"].startswith("de1-ovh")
    assert "youtube.com" in rules[2]["samples"]
    assert rules[3]["what"] == "торренты (по протоколу)"


def test_auto_profile_shows_order_and_probe(snapshot, servers_rows):
    """Для «Авто» главное — кого пробуем первым и что будет, если все лягут."""
    snapshot["hosts"] = [
        host("a", "🔒 de1", "de1.example.com", 443, INB_REALITY, isHidden=True),
        host("b", "🔒 pl1", "pl1.example.com", 443, INB_REALITY, isHidden=True),
        host("main", "Авто", "de1.example.com", 443, INB_REALITY, xrayJsonTemplateUuid="t1"),
    ]
    snapshot["templates"] = [template(
        "t1", "Auto", inject=[("wfa", "a"), ("wfb", "b")],
        rules=[{"type": "field", "network": "tcp,udp", "balancerTag": "bal"}],
        balancers=[{"tag": "bal", "selector": ["wfa", "wfb"], "fallbackTag": "wfa",
                    "strategy": {"type": "leastLoad", "settings": {"costs": [
                        {"match": "wfb", "value": 1}, {"match": "wfa", "value": 2}]}}}],
        observatory={"pingConfig": {"interval": "60s", "sampling": 3, "timeout": "5s",
                                    "destination": "http://example.com/204"},
                     "subjectSelector": ["wfa", "wfb"]},
    )]
    tunnel = only(infra.blueprint(snapshot, servers_rows), "Авто")

    assert tunnel["auto"] is True
    # Меньший вес идёт первым — это и есть «кого пробуем раньше».
    assert [step["tag"] for step in tunnel["order"]] == ["wfb", "wfa"]
    assert tunnel["order"][1]["fallback"] is True
    assert tunnel["probe"]["interval"] == "60s"
    assert tunnel["policy"] == {"buffer": 64, "idle": 180}


# ─────────────────────────── проверки здоровья ───────────────────────────


def test_disabled_source_host_is_reported(snapshot, servers_rows):
    """Так сломался ютуб: хост выключили, и выход молча пропал из профиля."""
    snapshot["hosts"] = [
        host("ru", "🔒 ru-exit", "de1.example.com", 443, INB_REALITY, isHidden=True, isDisabled=True),
        host("main", "Умный", "de1.example.com", 443, INB_REALITY, xrayJsonTemplateUuid="t1"),
    ]
    snapshot["templates"] = [template("t1", "Split", inject=[("ruv", "ru")])]
    problems = only(infra.blueprint(snapshot, servers_rows), "Умный")["problems"]

    assert any("выключен" in p for p in problems)


def test_rule_pointing_at_missing_outbound_is_reported(snapshot, servers_rows):
    """Правило ведёт на выход, которого нет — соединение просто падает."""
    snapshot["hosts"] = [
        host("a", "🔒 de1", "de1.example.com", 443, INB_REALITY, isHidden=True),
        host("main", "Умный", "de1.example.com", 443, INB_REALITY, xrayJsonTemplateUuid="t1"),
    ]
    snapshot["templates"] = [template("t1", "Split", inject=[("wfa", "a")], rules=[
        {"type": "field", "domain": ["domain:youtube.com"], "outboundTag": "ruv"},
    ])]
    problems = only(infra.blueprint(snapshot, servers_rows), "Умный")["problems"]

    assert any("ruv" in p and "нет" in p for p in problems)


def test_missing_fallback_is_reported(snapshot, servers_rows):
    snapshot["hosts"] = [
        host("a", "🔒 de1", "de1.example.com", 443, INB_REALITY, isHidden=True),
        host("main", "Авто", "de1.example.com", 443, INB_REALITY, xrayJsonTemplateUuid="t1"),
    ]
    snapshot["templates"] = [template("t1", "Auto", inject=[("wfa", "a")], balancers=[
        {"tag": "bal", "selector": ["wfa"], "fallbackTag": "wfz", "strategy": {}},
    ])]
    problems = only(infra.blueprint(snapshot, servers_rows), "Авто")["problems"]

    assert any("запасной выход" in p and "wfz" in p for p in problems)


def test_inbound_outside_any_squad_is_reported(snapshot, servers_rows):
    """Хост видно в панели, а людям он не раздаётся — туннель-призрак."""
    snapshot["hosts"] = [host("h1", "🇳🇱 Google", "88.0.0.1", 2096, INB_LONELY)]
    problems = only(infra.blueprint(snapshot, servers_rows), "🇳🇱 Google")["problems"]

    assert any("никому не раздаётся" in p for p in problems)


def test_dead_node_is_reported(snapshot, servers_rows):
    snapshot["hosts"] = [host("h1", "Cherry", "88.0.0.1", 443, INB_CS)]
    problems = only(infra.blueprint(snapshot, servers_rows), "Cherry")["problems"]

    assert any("не на связи" in p for p in problems)


def test_hidden_and_disabled_hosts_are_not_tunnels(snapshot, servers_rows):
    """Скрытые — служебные плечи, выключенные — вообще не отдаются."""
    snapshot["hosts"] = [
        host("a", "🔒 служебный", "de1.example.com", 443, INB_REALITY, isHidden=True),
        host("b", "выключенный", "de1.example.com", 443, INB_REALITY, isDisabled=True),
        host("c", "видимый", "de1.example.com", 443, INB_REALITY),
    ]
    titles = [t["title"] for t in infra.blueprint(snapshot, servers_rows)["tunnels"]]

    assert titles == ["видимый"]


# ─────────────────────────── серверы и деньги ───────────────────────────


def test_costs_are_brought_to_one_month():
    server = Server.objects.create(name="считаем расходы", price_month=Decimal("1000"))
    ServerCost.objects.create(server=server, title="ежемесячный", amount=Decimal("100"),
                              kind=ServerCost.Kind.MONTH)
    ServerCost.objects.create(server=server, title="годовой", amount=Decimal("1200"),
                              kind=ServerCost.Kind.YEAR)
    ServerCost.objects.create(server=server, title="разовый", amount=Decimal("5000"),
                              kind=ServerCost.Kind.ONCE)

    # Разовый в месячную сумму не входит: иначе расход «в месяц» врал бы.
    assert server.monthly_extra == Decimal("200.00")
    assert server.monthly_total == Decimal("1200.00")


def test_servers_block_sums_only_active_and_finds_nearest_renewal(snapshot):
    today = dt.date(2026, 9, 28)
    rows = [
        {"name": "de1-ovh", "panel_node_name": "de1-ovh", "price_month": Decimal("1000"),
         "monthly_total": Decimal("1000"), "is_active": True, "renew_at": today + dt.timedelta(days=30),
         "role": "exit_abroad", "role_display": "Выход за рубеж", "currency": "RUB"},
        {"name": "cs1-cherry", "panel_node_name": "cs1-cherry", "price_month": Decimal("500"),
         "monthly_total": Decimal("500"), "is_active": True, "renew_at": today + dt.timedelta(days=3),
         "role": "exit_abroad", "role_display": "Выход за рубеж", "currency": "RUB"},
        {"name": "старый", "panel_node_name": "", "price_month": Decimal("9000"),
         "monthly_total": Decimal("9000"), "is_active": False,
         "role": "other", "role_display": "Прочее", "currency": "RUB"},
    ]
    block = infra.servers(snapshot, rows)

    assert block["monthly"] == {"RUB": 1500}  # выключенный сервер в расход не идёт
    assert block["soonest"]["name"] == "cs1-cherry"
    assert block["alive"] == 1 and block["dead"] == 1  # cs1 в фикстуре не на связи


def test_traffic_limit_falls_back_to_panel(snapshot):
    """Лимит не проставлен руками — берём из панели, если она его знает."""
    snapshot["nodes"][0]["trafficLimitBytes"] = 20 * infra.TB
    rows = [{"name": "de1-ovh", "panel_node_name": "de1-ovh", "is_active": True,
             "role": "exit_abroad", "role_display": "Выход", "currency": "RUB"}]
    row = infra.servers(snapshot, rows)["rows"][0]

    assert row["limit_tb"] == 20.0
    assert row["used_share"] == 25  # 5 ТБ из 20


# ─────────────────────────── ручка и админка ───────────────────────────


def test_infra_api_works_without_panel(admin_client, monkeypatch):
    """Панель лежит — экран всё равно открывается.

    Деньги и сроки к панели не привязаны и остаются полезными, а туннели
    честно показываются пустыми, а не роняют страницу пятисоткой.
    """
    monkeypatch.setattr(infra, "_CACHE", {"data": None, "at": None})
    monkeypatch.setattr(
        "nexvpn.remnawave.client.RemnawaveClient.list_nodes",
        lambda self: (_ for _ in ()).throw(RuntimeError("панель недоступна")),
    )
    Server.objects.create(name="тестовый", price_month=Decimal("1000"), panel_node_name="de1-ovh")

    body = admin_client.get("/dash/api/infra/").json()

    assert body["panel_ok"] is False
    assert body["blueprint"]["tunnels"] == []
    assert body["servers"]["monthly"] == {"RUB": 1000}


def test_infra_api_requires_staff(client):
    """Ручка отдаёт JSON, а не редирект на HTML-логин: иначе fetch падает молча."""
    response = client.get("/dash/api/infra/")

    assert response.status_code == 403
    assert response.json()["login"] == "/admin/login/"


def test_server_admin_page_opens(admin_client):
    server = Server.objects.create(name="тестовый", provider="OVH", price_month=Decimal("1000"))
    ServerCost.objects.create(server=server, title="доп. IP", amount=Decimal("100"))

    listing = admin_client.get("/admin/nexvpn/server/")
    card = admin_client.get(f"/admin/nexvpn/server/{server.pk}/change/")

    assert listing.status_code == 200 and "тестовый" in listing.content.decode()
    assert card.status_code == 200 and "доп. IP" in card.content.decode()


def test_known_servers_are_seeded_by_migration():
    """Список не должен начинаться с пустой страницы."""
    names = set(Server.objects.values_list("name", flat=True))

    assert {"de1-ovh", "ru1-timeweb", "relay-iot", "nex-prod", "nex-vds"} <= names
    assert Server.objects.get(name="eu2-alexhost").is_active is False


def test_dashboard_page_carries_the_infra_screen(admin_client):
    """Страница должна содержать оба блока и ходить за ними на свою ручку."""
    html = admin_client.get("/dash/").content.decode()

    assert "Серверы и деньги" in html
    assert "Как устроены туннели" in html
    assert "/dash/api/infra/" in html
    # Блок не привязан к датам в шапке — и не должен перерисовываться при их смене.
    assert "loadInfra()" in html


def test_currencies_are_not_summed_together(snapshot):
    """Рубли и евро — разные числа. Одной суммой это было бы враньё."""
    rows = [
        {"name": "ру", "panel_node_name": "", "monthly_total": Decimal("1000"),
         "is_active": True, "currency": "RUB", "role": "other", "role_display": "Прочее"},
        {"name": "евро", "panel_node_name": "", "monthly_total": Decimal("20"),
         "is_active": True, "currency": "EUR", "role": "other", "role_display": "Прочее"},
    ]

    assert infra.servers(snapshot, rows)["monthly"] == {"EUR": 20, "RUB": 1000}
