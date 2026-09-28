"""Связки «российский вход → зарубежный выход → протокол».

Отвечает на вопрос «что оставлять, а что гасить». Названия туннелей для этого
плохая опора: на одну связку приходится несколько туннелей, а «Авто» вообще
перебирает их все, и человек, сидящий на автоподборе, числится сразу на всех
плечах. Поэтому считаем связки, а туннели к ним привязываем, а не наоборот.

Байты приходят со счётчиков ядра на нодах, люди — из журнала доступа Xray.
Пробы автовыбора отделены: приложение дёргает generate_204 через каждое плечо
профиля, и без фильтра счёт людей завышался примерно на треть.
"""

from collections import defaultdict

from nexvpn.dashboard import infra
from nexvpn.models import LinkUsageDay

# Адрес плеча в подписке -> вход, под которым он считается на выходной ноде.
# Сопоставление явное: угадывать по имени значит однажды завести новый релей
# и молча потерять его трафик.
ENTRY_BY_ADDRESS = {
    "185.71.198.238": "atlex",
    "ru1.pineferry.com": "timeweb",
    "62.113.42.120": "timeweb",
    "ru2.pineferry.com": "eurobyte_a",
    "95.142.44.105": "eurobyte_a",
    "ru2b.pineferry.com": "eurobyte_b",
    "185.105.109.159": "eurobyte_b",
}
PROTO_BY_INBOUND = {
    "VLESS-REALITY": "reality", "VLESS-GRPC": "grpc", "VLESS-REALITY-VK": "trojan_or_vk",
    "RU-REALITY": "ru_reality", "Hysteria2": "hysteria", "Hysteria2-Obfs": "hysteria_obfs",
    "CS-VLESS-REALITY": "reality", "CS-VLESS-GRPC": "grpc",
    "CS-VLESS-REALITY-GOOGLE": "reality_google",
    "CS-Hysteria2": "hysteria", "CS-Hysteria2-Obfs": "hysteria_obfs",
    "RU2-GRPC-A": "grpc", "RU2-GRPC-B": "grpc_alt",
    "RU2-TROJAN-A": "trojan_or_vk", "RU2-TROJAN-B": "trojan_or_vk",
    "RU2-HY2-A": "hysteria_obfs", "RU2-HY2-B": "hysteria_obfs",
}
PROTO_TITLE = {
    "reality": "REALITY", "ru_reality": "REALITY (РФ)", "reality_google": "REALITY Google",
    "grpc": "gRPC", "grpc_alt": "gRPC 8080", "trojan_or_vk": "Trojan / VK",
    "hysteria": "Hysteria2", "hysteria_obfs": "Hysteria2 обф.",
}
ENTRY_TITLE = {
    "atlex": "Atlex", "timeweb": "Timeweb", "eurobyte_a": "EuroByte",
    "eurobyte_b": "EuroByte 2", "direct": "напрямую",
}
# Порядок колонок — от самых ходовых к редким, чтобы таблица читалась слева направо.
PROTO_ORDER = ["reality", "grpc", "hysteria_obfs", "trojan_or_vk",
               "hysteria", "grpc_alt", "ru_reality", "reality_google"]


def _rows(since, until):
    return LinkUsageDay.objects.filter(date__gte=since, date__lte=until)


def totals(since, until):
    """Свод по связкам за период: байты складываем, людей объединяем.

    Уникальных за неделю нельзя получить сложением уникальных за каждый день,
    поэтому и храним списки id, а не числа.
    """
    acc = {}
    for row in _rows(since, until):
        key = (row.entry, row.exit_node, row.protocol)
        cell = acc.setdefault(key, {
            "entry": row.entry, "exit_node": row.exit_node, "protocol": row.protocol,
            "bytes": 0, "connections": 0, "probes": 0, "users": set(), "probing": set(),
        })
        cell["bytes"] += row.bytes_in + row.bytes_out
        cell["connections"] += row.connections
        cell["probes"] += row.probes
        cell["users"].update(row.users or [])
        cell["probing"].update(row.probing or [])
    return acc


def top_links(since, until, limit=30):
    """Топ связок: комбинация, трафик, уникальные люди."""
    out = []
    for cell in totals(since, until).values():
        out.append({
            "entry": ENTRY_TITLE.get(cell["entry"], cell["entry"]),
            "exit": cell["exit_node"],
            "protocol": PROTO_TITLE.get(cell["protocol"], cell["protocol"]),
            "title": "%s → %s · %s" % (
                ENTRY_TITLE.get(cell["entry"], cell["entry"]),
                cell["exit_node"],
                PROTO_TITLE.get(cell["protocol"], cell["protocol"]),
            ),
            "bytes": cell["bytes"],
            "users": len(cell["users"]),
            "probing": len(cell["probing"] - cell["users"]),
            "connections": cell["connections"],
        })
    out.sort(key=lambda r: -r["bytes"])
    return out[:limit]


def tunnel_matrix(since, until, snapshot=None):
    """Туннели из подписки × протоколы. «Авто» исключены намеренно.

    У автоподбора нет своего трафика: он раскладывается по тем же плечам, что
    и у ручных туннелей, и показывать его отдельной строкой значило бы считать
    один и тот же байт дважды.
    """
    snapshot = snapshot if snapshot is not None else infra.snapshot()
    blueprint = infra.blueprint(snapshot)
    acc = totals(since, until)

    protocols, rows = set(), []
    for tunnel in blueprint.get("tunnels", []):
        if tunnel.get("auto"):
            continue
        cells, total_bytes, users = {}, 0, set()
        for leg in tunnel.get("legs", []):
            entry = ENTRY_BY_ADDRESS.get(leg.get("address"), "direct")
            proto = PROTO_BY_INBOUND.get(leg.get("inbound"))
            exit_node = (leg.get("exit") or {}).get("node")
            if not proto or not exit_node:
                continue
            cell = acc.get((entry, exit_node, proto))
            if not cell:
                continue
            protocols.add(proto)
            slot = cells.setdefault(proto, {"bytes": 0, "users": set()})
            slot["bytes"] += cell["bytes"]
            slot["users"].update(cell["users"])
            total_bytes += cell["bytes"]
            users.update(cell["users"])
        rows.append({
            "title": tunnel.get("title"),
            "position": tunnel.get("position"),
            "cells": {p: {"bytes": c["bytes"], "users": len(c["users"])} for p, c in cells.items()},
            "bytes": total_bytes,
            "users": len(users),
        })
    rows.sort(key=lambda r: -r["bytes"])
    columns = [p for p in PROTO_ORDER if p in protocols]
    return {
        "columns": [{"key": p, "title": PROTO_TITLE.get(p, p)} for p in columns],
        "rows": rows,
    }
