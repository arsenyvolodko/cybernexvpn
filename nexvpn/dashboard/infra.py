"""Инфраструктура: серверы, деньги и устройство туннелей. Только чтение.

Два вопроса, на которые отвечает этот модуль.

**Сколько стоит железо и когда платить.** Панель знает про ноды: живы ли,
сколько на них людей, сколько прокачали. Про деньги она не знает ничего, а
половина серверов (прод, панель, релеи, зеркало подписки) вообще не ноды.
Поэтому список ведётся в `Server`, а панель к нему прикладывается сбоку.

**Через что на самом деле идёт каждый туннель.** Это не читается ни из одного
места панели: видимый хост даёт только название и адрес входа, выходы
подставляются из других хостов через шаблон подписки, а куда ведёт российский
релей — определяется адресом, профилем конфига и, в неоднозначном случае,
названием хоста. Здесь всё это сводится в одну понятную картинку.

Разбор написан чистыми функциями над снимком панели (`blueprint`), поэтому
проверяется на фикстуре без сети.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import logging
import re
import urllib.request
from xml.etree import ElementTree

from django.core.cache import cache
from django.utils import timezone

# Официальный ЦБ в XML — он доступен оттуда, где зеркало cbr-xml-daily уже нет.
CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
# Запасной на случай, если ЦБ не ответит: отдаёт курсы ОТ рубля, поэтому 1/курс.
FX_FALLBACK_URL = "https://open.er-api.com/v6/latest/RUB"
FX_CACHE_KEY = "nex:fx-rates"

logger = logging.getLogger(__name__)

# Панель за Cloudflare и отвечает не мгновенно, а состав туннелей меняется
# раз в неделю. Держим короткий кэш в памяти процесса.
_CACHE: dict[str, object] = {"data": None, "at": None}
_TTL = dt.timedelta(minutes=5)

TB = 1024 ** 4

# Служебные выходы есть в каждом профиле и туннелями не являются.
SERVICE_TAGS = {"direct", "block"}


# ─────────────────────── человеческие подписи ───────────────────────


def transport_label(inbound: dict) -> str:
    """«VLESS · gRPC · TLS» вместо `vless/grpc/tls`."""
    kind = (inbound.get("type") or "").lower()
    network = (inbound.get("network") or "").lower()
    security = (inbound.get("security") or "").lower()
    if kind == "hysteria":
        return "Hysteria2 · QUIC"
    parts = [kind.upper() if kind else "?"]
    parts.append({"grpc": "gRPC", "tcp": "TCP", "ws": "WebSocket"}.get(network, network or "?"))
    parts.append({"reality": "REALITY", "tls": "TLS"}.get(security, security or "без TLS"))
    return " · ".join(parts)


def transport_short(inbound: dict) -> str:
    """Одно слово для подписи: три плеча на одну ноду иначе неразличимы."""
    if (inbound.get("type") or "").lower() == "hysteria":
        return "Hysteria2"
    network = (inbound.get("network") or "").lower()
    if network == "grpc":
        return "gRPC"
    return "REALITY" if (inbound.get("security") or "").lower() == "reality" else "TLS"


def is_multiplexed(inbound: dict) -> bool:
    """Складывает ли транспорт много соединений в одно.

    Не украшение: туннель умирает на лавине переподключений, когда на каждое
    соединение приложения поднимается своё TCP с рукопожатием. gRPC (потоки
    HTTP/2) и Hysteria2 (потоки QUIC) этого не делают, обычный TCP делает.
    """
    return (inbound.get("network") or "").lower() in ("grpc", "hysteria")


_RU_ZONE = re.compile(r"\\\.(ru|su|рф|xn--p1ai)\$")

# Темы, по которым обычно и режут трафик. Список намеренно короткий:
# подписать «ютуб» полезно, а выдумывать категорию под каждый домен — нет.
_THEMES = (
    ("ютуб", ("youtube", "googlevideo", "ytimg", "ggpht")),
    ("тикток", ("tiktok", "musical.ly", "byteoversea", "bytedance", "byteimg")),
    ("Gemini", ("gemini", "generativelanguage", "aistudio", "ai.google.dev")),
    ("соцсети Meta", ("instagram", "facebook", "whatsapp", "fbcdn")),
    ("ВКонтакте", ("vk.com", "userapi", "vkuser")),
)


def domain_theme(domains: list[str]) -> str:
    """О чём этот список доменов. Тем может быть несколько — перечисляем все."""
    joined = " ".join(domains).lower()
    if any(_RU_ZONE.search(d) for d in domains) or ".ru$" in joined:
        return "рунет"
    found = [title for title, needles in _THEMES if any(needle in joined for needle in needles)]
    if not found:
        return ""
    if len(found) == 1:
        return found[0]
    return ", ".join(found[:-1]) + " и " + found[-1]


def _samples(domains: list[str], count: int = 4) -> list[str]:
    clean = [d.split(":", 1)[-1] for d in domains if not d.startswith("regexp:")]
    return clean[:count]


# ─────────────────────────── разбор снимка ───────────────────────────


def _index(snapshot: dict) -> dict:
    """Быстрые справочники по снимку панели."""
    inbounds = {i["uuid"]: i for i in snapshot.get("inbounds", [])}
    nodes = snapshot.get("nodes", [])
    profile_nodes: dict[str, list[dict]] = {}
    for profile in snapshot.get("profiles", []):
        names = []
        for node in profile.get("nodes") or []:
            names.append(node if isinstance(node, str) else node.get("name", ""))
        profile_nodes[profile.get("uuid", "")] = [n for n in nodes if n.get("name") in names]
    # Некоторые сборки панели не отдают ноды внутри профиля — тогда берём
    # привязку с самой ноды, она есть всегда.
    for node in nodes:
        uuid = (node.get("configProfile") or {}).get("activeConfigProfileUuid")
        if uuid and node not in profile_nodes.get(uuid, []):
            profile_nodes.setdefault(uuid, []).append(node)
    return {
        "inbounds": inbounds,
        "nodes": nodes,
        "nodes_by_address": {n.get("address"): n for n in nodes},
        "profile_nodes": profile_nodes,
        "hosts": {h["uuid"]: h for h in snapshot.get("hosts", [])},
        "templates": {t["uuid"]: t for t in snapshot.get("templates", []) if t.get("uuid")},
        "squad_inbounds": {
            uuid
            for squad in snapshot.get("squads", [])
            for uuid in [(i if isinstance(i, str) else i.get("uuid")) for i in (squad.get("inbounds") or [])]
        },
    }


_ARROW = re.compile(r"[→>]\s*([a-zA-Z0-9_-]+)")


def _leg(host: dict, index: dict, servers_by_address: dict, servers_by_node: dict) -> dict:
    """Одно плечо туннеля: откуда заходим и где выходим.

    Вход — адрес хоста. Выход — нода, которая держит инбаунд этого хоста.
    Совпали адреса — значит заходим прямо на неё. Не совпали — перед нами
    российский релей, и выходную ноду приходится доопределять: если профиль
    конфига крутится на одной ноде, она и есть ответ; если на нескольких —
    смотрим в название хоста, у нас они подписаны «relay-iot → de1 gRPC».
    Не вышло — честно говорим «не определено», а не угадываем.
    """
    inbound = index["inbounds"].get((host.get("inbound") or {}).get("configProfileInboundUuid")) or {}
    address = host.get("address") or ""
    candidates = index["profile_nodes"].get(inbound.get("profileUuid", ""), [])

    direct = index["nodes_by_address"].get(address)
    exit_node, source = None, ""
    if direct is not None and direct in candidates:
        exit_node, source = direct, "адрес"
    elif len(candidates) == 1:
        exit_node, source = candidates[0], "профиль"
    else:
        match = _ARROW.search(host.get("remark") or "")
        if match:
            token = match.group(1).lower()
            for node in candidates:
                if (node.get("name") or "").lower().startswith(token):
                    exit_node, source = node, "название"
                    break

    entry_server = servers_by_address.get(address)
    exit_server = servers_by_node.get(exit_node.get("name")) if exit_node else None
    return {
        "host_uuid": host.get("uuid"),
        "remark": host.get("remark") or "",
        "address": address,
        "port": host.get("port"),
        "inbound": inbound.get("tag") or "?",
        "transport": transport_label(inbound) if inbound else "—",
        "transport_short": transport_short(inbound) if inbound else "—",
        "multiplex": is_multiplexed(inbound) if inbound else False,
        "entry": {
            "server": (entry_server or {}).get("name") or address,
            "country": (entry_server or {}).get("country") or "",
            "role": (entry_server or {}).get("role") or "",
            "known": entry_server is not None,
        },
        "exit": {
            "node": exit_node.get("name") if exit_node else "",
            "server": (exit_server or {}).get("name") or (exit_node.get("name") if exit_node else ""),
            "country": (exit_server or {}).get("country") or "",
            "online": exit_node.get("usersOnline") if exit_node else None,
            "alive": bool(exit_node.get("isConnected")) if exit_node else None,
            "source": source,
        },
        "direct": source == "адрес",
        "disabled": bool(host.get("isDisabled")),
        "served": (host.get("inbound") or {}).get("configProfileInboundUuid") in index["squad_inbounds"],
    }


def _routing(rules: list[dict], labels: dict[str, str]) -> list[dict]:
    """Правила маршрутизации словами. Порядок важен — он же порядок проверки."""
    out = []
    for rule in rules:
        domains = rule.get("domain") or []
        ips = rule.get("ip") or []
        tag = rule.get("outboundTag") or ""
        target = labels.get(tag) or ("в балансировщик" if rule.get("balancerTag") else tag)

        if tag == "block":
            what = "QUIC (udp/443)" if rule.get("network") == "udp" else "трафик"
            out.append({"what": what, "where": "блокируется", "kind": "block"})
            continue
        if rule.get("protocol") == ["bittorrent"]:
            out.append({"what": "торренты (по протоколу)", "where": target or "мимо VPN", "kind": "direct"})
            continue
        if str(rule.get("port") or "").startswith("6881"):
            out.append({"what": "торрент-порты", "where": target or "мимо VPN", "kind": "direct"})
            continue
        if ips and not domains:
            out.append({"what": f"локальные сети ({len(ips)})", "where": "мимо VPN", "kind": "direct"})
            continue
        if domains:
            theme = domain_theme(domains)
            what = f"{theme} — {len(domains)} доменов" if theme else f"{len(domains)} доменов"
            out.append({
                "what": what,
                "where": "мимо VPN" if tag == "direct" else target,
                "kind": "direct" if tag == "direct" else "leg",
                "samples": _samples(domains),
            })
            continue
        out.append({"what": "всё остальное", "where": target, "kind": "rest"})
    return out


def _probe(config: dict) -> dict | None:
    observatory = config.get("burstObservatory") or config.get("observatory")
    if not observatory:
        return None
    ping = observatory.get("pingConfig") or {}
    return {
        "interval": ping.get("interval") or "—",
        "sampling": ping.get("sampling"),
        "timeout": ping.get("timeout") or "—",
        "destination": ping.get("destination") or "—",
        "subjects": observatory.get("subjectSelector") or [],
    }


def _tunnel(host: dict, index: dict, servers_by_address: dict, servers_by_node: dict) -> dict:
    """Один туннель так, как его видит человек в приложении."""
    template = index["templates"].get(host.get("xrayJsonTemplateUuid") or "")
    config = (template or {}).get("templateJson") or {}
    if isinstance(config, str):
        import json

        try:
            config = json.loads(config)
        except ValueError:
            config = {}

    problems: list[str] = []
    notes: list[str] = []
    legs: list[dict] = []
    if template:
        # Профиль собран из шаблона: выходы подставляются из других хостов.
        for entry in (config.get("remnawave") or {}).get("injectHosts") or []:
            tag = entry.get("tagPrefix") or "?"
            for uuid in (entry.get("selector") or {}).get("values") or []:
                source = index["hosts"].get(uuid)
                if source is None:
                    problems.append(f"выход {tag}: хост {uuid[:8]} удалён из панели")
                    continue
                leg = _leg(source, index, servers_by_address, servers_by_node)
                leg["tag"] = tag
                if leg["disabled"]:
                    problems.append(
                        f"выход {tag}: хост «{leg['remark']}» выключен — в профиль он не попадёт"
                    )
                legs.append(leg)
    else:
        leg = _leg(host, index, servers_by_address, servers_by_node)
        leg["tag"] = "proxy"
        legs.append(leg)

    labels = {
        leg["tag"]: " · ".join(filter(None, [
            leg["exit"]["server"] or leg["remark"] or leg["tag"],
            leg["transport_short"] if leg["transport_short"] != "—" else "",
        ]))
        for leg in legs
    }
    labels.setdefault("direct", "мимо VPN")

    balancers = (config.get("routing") or {}).get("balancers") or []
    tags = {leg["tag"] for leg in legs}
    order: list[dict] = []
    for balancer in balancers:
        costs = {c.get("match"): c.get("value") for c in
                 ((balancer.get("strategy") or {}).get("settings") or {}).get("costs") or []}
        fallback = balancer.get("fallbackTag") or ""
        for tag in balancer.get("selector") or []:
            order.append({"tag": tag, "weight": costs.get(tag), "fallback": tag == fallback,
                          "label": labels.get(tag, tag)})
            if tag not in tags:
                problems.append(f"балансировщик перебирает выход {tag}, которого в профиле нет")
        if fallback and fallback not in tags and fallback != "direct":
            problems.append(f"запасной выход балансировщика {fallback} не существует")
        if fallback == "direct":
            problems.append("запасной выход — «мимо VPN»: если все серверы лягут, трафик пойдёт открытым")
        order.sort(key=lambda row: (row["weight"] is None, row["weight"]))

    for rule in (config.get("routing") or {}).get("rules") or []:
        tag = rule.get("outboundTag")
        if tag and tag not in SERVICE_TAGS and tag not in tags:
            problems.append(f"правило маршрутизации ведёт на выход {tag}, которого в профиле нет")

    for leg in legs:
        if not leg["exit"]["node"]:
            notes.append(
                f"выход {leg['tag']}: по данным панели не понять, на какой сервер ведёт релей "
                f"{leg['address']}:{leg['port']} — допишите в название хоста «→ имя-ноды»"
            )
        if not leg["served"]:
            problems.append(f"выход {leg['tag']}: инбаунд {leg['inbound']} никому не раздаётся")
        if leg["exit"]["alive"] is False:
            problems.append(f"выход {leg['tag']}: нода {leg['exit']['node']} не на связи")

    policy = ((config.get("policy") or {}).get("levels") or {}).get("0") or {}
    return {
        "title": host.get("remark") or "—",
        "position": host.get("viewPosition"),
        "template": (template or {}).get("name") or "",
        "auto": bool(balancers),
        "legs": legs,
        "order": order,
        "routing": _routing((config.get("routing") or {}).get("rules") or [], labels),
        "probe": _probe(config),
        "domain_strategy": (config.get("routing") or {}).get("domainStrategy") or "",
        "policy": {"buffer": policy.get("bufferSize"), "idle": policy.get("connIdle")} if policy else None,
        "multiplex": all(leg["multiplex"] for leg in legs) if legs else False,
        "problems": problems,
        "notes": notes,
    }


def blueprint(snapshot: dict, servers: list[dict] | None = None) -> dict:
    """Устройство всех туннелей, которые реально видит человек в подписке.

    `servers` — наши серверы из базы, чтобы подписать адреса именами. Без них
    разбор всё равно работает, просто вместо названий будут адреса.
    """
    servers = servers or []
    by_address = {s["address"]: s for s in servers if s.get("address")}
    by_node = {s["panel_node_name"]: s for s in servers if s.get("panel_node_name")}
    # В базе адрес записан как удобнее (у кого домен, у кого IP), а хосты
    # панели ссылаются то так, то так. Добавляем адрес ноды как второй ключ,
    # иначе половина плеч осталась бы без названия сервера.
    for node in snapshot.get("nodes", []):
        server = by_node.get(node.get("name"))
        if server and node.get("address"):
            by_address.setdefault(node["address"], server)
    index = _index(snapshot)

    visible = [
        host for host in snapshot.get("hosts", [])
        if not host.get("isHidden") and not host.get("isDisabled")
    ]
    visible.sort(key=lambda h: h.get("viewPosition") or 0)
    tunnels = [_tunnel(host, index, by_address, by_node) for host in visible]
    return {
        "tunnels": tunnels,
        "problems": sum(len(t["problems"]) for t in tunnels),
        "notes": sum(len(t["notes"]) for t in tunnels),
    }


# ─────────────────────────── серверы и деньги ───────────────────────────


def fx_rates():
    """Курсы к рублю с сайта ЦБ. Возвращает None, если узнать не удалось.

    Выдумывать запасной курс нельзя: цифра «расход в месяц» выглядит точной,
    и подставить в неё прошлогодний курс хуже, чем честно сказать «не знаю».
    """
    cached = cache.get(FX_CACHE_KEY)
    if cached is not None:
        return cached or None
    rates = _rates_from_cbr() or _rates_from_fallback()
    # Неудачу тоже кэшируем, но ненадолго: иначе будем долбить ЦБ на каждый показ.
    cache.set(FX_CACHE_KEY, rates or {}, 6 * 3600 if rates else 600)
    return rates


def _rates_from_cbr():
    try:
        with urllib.request.urlopen(CBR_URL, timeout=8) as response:
            tree = ElementTree.fromstring(response.read())
        rates = {"RUB": 1.0}
        for item in tree.findall("Valute"):
            code = (item.findtext("CharCode") or "").upper()
            if code not in ("EUR", "USD"):
                continue
            # ЦБ отдаёт запятую как разделитель дробной части.
            value = float((item.findtext("Value") or "0").replace(",", "."))
            nominal = float(item.findtext("Nominal") or 1)
            rates[code] = value / nominal
        return rates if len(rates) == 3 else None
    except Exception as exc:                      # noqa: BLE001 — курс не критичен
        logger.warning("Курс ЦБ не получен: %s", exc)
        return None


def _rates_from_fallback():
    """Запасной источник отдаёт курсы ОТ рубля, поэтому берём обратные."""
    try:
        with urllib.request.urlopen(FX_FALLBACK_URL, timeout=8) as response:
            data = json.load(response)
        rub_rates = data.get("rates") or {}
        rates = {"RUB": 1.0}
        for code in ("EUR", "USD"):
            per_rub = float(rub_rates[code])
            rates[code] = 1 / per_rub
        return rates
    except Exception as exc:                      # noqa: BLE001
        logger.warning("Запасной курс не получен: %s", exc)
        return None


def next_renewal(renew_at, today):
    """Следующее продление по числу месяца.

    Хостинг продлевается каждый месяц одного и того же числа, поэтому хранить
    и показывать полную дату бессмысленно: через месяц она превратится в
    «просрочено». Берём из даты только число и ищем ближайшее его наступление.
    """
    if not renew_at:
        return None, None
    day = renew_at.day
    year, month = today.year, today.month
    for _ in range(2):
        last = calendar.monthrange(year, month)[1]
        candidate = dt.date(year, month, min(day, last))
        if candidate >= today:
            return candidate, (candidate - today).days
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return None, None


def _to_rub(monthly: dict[str, float]):
    """Свести расход к одной цифре в рублях. None, если курс неизвестен."""
    if not monthly:
        return 0.0
    rates = fx_rates()
    if rates is None:
        return None
    total = 0.0
    for code, value in monthly.items():
        rate = rates.get(code)
        if rate is None:
            return None
        total += value * rate
    return round(total, 2)


def servers(snapshot: dict, rows: list[dict]) -> dict:
    """Наши серверы с прикрученной к ним живой статистикой панели."""
    nodes = {n.get("name"): n for n in snapshot.get("nodes", [])}
    today = timezone.localdate()
    # Считаем по каждой валюте отдельно: у нас есть счета и в рублях, и в евро,
    # а складывать их одной цифрой значило бы рисовать число, которого нет.
    monthly: dict[str, float] = {}
    out, soonest = [], None
    alive = dead = 0

    for row in rows:
        node = nodes.get(row.get("panel_node_name") or "")
        used = node.get("trafficUsedBytes") if node else None
        limit_tb = row.get("traffic_limit_tb")
        # Лимит из панели берём только если он там выставлен: ноль там значит
        # «не следим», а не «ноль байт».
        node_limit = (node or {}).get("trafficLimitBytes") or 0
        if limit_tb is None and node_limit:
            limit_tb = round(node_limit / TB, 2)

        renew = row.get("renew_at")
        next_due, days_left = next_renewal(renew, today)
        if next_due and (soonest is None or next_due < soonest["date"]):
            soonest = {"date": next_due, "day": renew.day, "name": row["name"], "days": days_left}

        if row.get("panel_node_name"):
            if node is None:
                pass
            elif node.get("isConnected"):
                alive += 1
            else:
                dead += 1

        total = float(row.get("monthly_total") or 0)
        currency = row.get("currency") or "RUB"
        if row.get("is_active") and total:
            monthly[currency] = monthly.get(currency, 0.0) + total

        out.append({
            "id": row.get("id"),
            "name": row["name"],
            "provider": row.get("provider") or "",
            "role": row.get("role_display") or "",
            "role_key": row.get("role") or "",
            "country": row.get("country") or "",
            "city": row.get("city") or "",
            "address": row.get("address") or "",
            "node": row.get("panel_node_name") or "",
            "alive": bool(node.get("isConnected")) if node else None,
            "online": node.get("usersOnline") if node else None,
            "used_bytes": used,
            "limit_tb": float(limit_tb) if limit_tb is not None else None,
            "used_share": round(used / (float(limit_tb) * TB) * 100) if (used and limit_tb) else None,
            "price": float(row.get("price_month") or 0) or None,
            "extra": float(row.get("monthly_extra") or 0) or None,
            "total": total or None,
            "currency": row.get("currency") or "RUB",
            "renew_at": next_due.isoformat() if next_due else None,
            "renew_day": renew.day if renew else None,
            "days_left": days_left,
            "auto_renew": bool(row.get("auto_renew")),
            "is_active": bool(row.get("is_active")),
            "costs": row.get("costs") or [],
            "note": row.get("note") or "",
        })

    out.sort(key=lambda s: (not s["is_active"], s["role_key"], s["name"]))
    return {
        "rows": out,
        "monthly": {code: value for code, value in sorted(monthly.items())},
        "monthly_rub": _to_rub(monthly),
        "alive": alive,
        "dead": dead,
        "soonest": {"name": soonest["name"], "date": soonest["date"].isoformat(),
                    "day": soonest["day"], "days": soonest["days"]}
        if soonest else None,
    }


# ─────────────────────────────── снимок ───────────────────────────────


def snapshot(force: bool = False) -> dict:
    """Снимок панели. Кэш на пять минут — страница не должна её долбить."""
    from nexvpn.remnawave.client import RemnawaveClient

    fetched_at = _CACHE["at"]
    if not force and fetched_at is not None and timezone.now() - fetched_at < _TTL:
        return _CACHE["data"]

    client = RemnawaveClient()
    try:
        hosts = client.list_hosts()
        # Содержимое шаблона панель отдаёт отдельным запросом на каждый, а их
        # больше десятка. Читаем только те, на которые ссылается живой хост:
        # остальные (mihomo, clash, singbox и старьё) для разбора не нужны.
        used = {h.get("xrayJsonTemplateUuid") for h in hosts if h.get("xrayJsonTemplateUuid")}
        data = {
            "nodes": client.list_nodes(),
            "hosts": hosts,
            "inbounds": client.list_inbounds(),
            "squads": client.list_internal_squads(),
            "profiles": client.list_config_profiles(),
            "templates": client.list_subscription_templates(with_json=True, only_uuids=used),
        }
    except Exception as exc:
        # Панель лежит — страница всё равно должна открыться: блок с деньгами
        # к ней не привязан и остаётся полезным.
        logger.warning("Панель недоступна, инфраструктура покажется без неё: %s", exc)
        data = _CACHE["data"] or {"nodes": [], "hosts": [], "inbounds": [],
                                  "squads": [], "profiles": [], "templates": []}
    _CACHE["data"] = data
    _CACHE["at"] = timezone.now()
    return data


def server_rows() -> list[dict]:
    """Серверы из базы в том виде, в каком их ждёт `servers()`."""
    from nexvpn.models import Server

    rows = []
    for server in Server.objects.prefetch_related("costs").all():
        rows.append({
            "id": server.pk,
            "name": server.name,
            "provider": server.provider,
            "address": server.address,
            "country": server.country,
            "city": server.city,
            "role": server.role,
            "role_display": server.get_role_display(),
            "traffic_limit_tb": server.traffic_limit_tb,
            "price_month": server.price_month,
            "monthly_extra": server.monthly_extra,
            "monthly_total": server.monthly_total,
            "currency": server.currency,
            "renew_at": server.renew_at,
            "auto_renew": server.auto_renew,
            "is_active": server.is_active,
            "panel_node_name": server.panel_node_name,
            "note": server.note,
            "costs": [
                {"title": c.title, "amount": float(c.amount), "currency": c.currency,
                 "kind": c.get_kind_display()}
                for c in server.costs.all()
            ],
        })
    return rows


def overview() -> dict:
    """Всё для экрана «Инфраструктура» одним куском."""
    snap = snapshot()
    rows = server_rows()
    return {
        "servers": servers(snap, rows),
        "blueprint": blueprint(snap, rows),
        "panel_ok": bool(snap.get("nodes")),
    }
