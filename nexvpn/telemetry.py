"""Снимки использования: кто, когда и через какой туннель ходит.

Зачем это нужно. Сбой, который волнует больше всего — когда туннель душит DPI, —
на сервере не оставляет никаких следов: пакет до ноды просто не доходит. Значит
«человек попробовал и не смог» серверными метриками не увидеть в принципе.

Зато прекрасно видно обратное: кто подключился и качает. Отсюда сильная
косвенная метрика — **молчащие**: подписка активна, устройство заведено, а
трафика ноль. Это и есть список тех, у кого не работает.

Панель хранит только последнее значение счётчиков, историю не отдаёт. Поэтому
историю копим сами: раз в несколько минут забираем срез и раскладываем прирост
по суткам и нодам.

Дальше ноды панель не видит: трафик она отдаёт с точностью до ноды, а на eu1 и
eu2 у нас по пять туннелей. «Какой именно туннель» приезжает вторым, встречным
потоком — сборщик на ноде читает access log Xray и присылает сюда счётчики по
инбаундам (`record_inbound_usage`). Байтов в этом потоке нет и не будет: Xray
считает трафик по пользователю и по инбаунду двумя счётчиками, которые никогда
не пересекаются.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Count, F, Max, Q, Sum
from django.utils.dateparse import parse_datetime
from django.utils.timezone import localdate, now

from nexvpn.enums import PanelSyncStatusEnum
from nexvpn.models import InboundUsageDay, NexUser, NodeUsageDay, PanelPresence, Subscription
from nexvpn.remnawave.client import RemnawaveClient

logger = logging.getLogger(__name__)

# Сколько считать «молчанием»: сутки без единого байта при живой подписке.
SILENT_AFTER = timedelta(hours=24)


@dataclass
class SnapshotResult:
    seen: int
    online: int
    traffic_delta: int
    unknown_users: int
    vanished: int = 0


def _as_datetime(value) -> datetime | None:
    if not value:
        return None
    return parse_datetime(value) if isinstance(value, str) else value


def take_snapshot(client: RemnawaveClient | None = None) -> SnapshotResult:
    """Забрать срез из панели и разложить прирост по нодам."""
    client = client or RemnawaveClient()
    node_names = {node["uuid"]: node.get("name", "") for node in client.list_nodes()}
    panel_users = client.iter_users()

    # Связь панель ↔ наша база идёт через panel_user_id подписки: username в
    # панели формируется из pk, но полагаться на разбор строки не хочется.
    by_panel_id = dict(
        Subscription.objects.exclude(panel_user_id=None).values_list("panel_user_id", "user_id")
    )
    presence = {p.user_id: p for p in PanelPresence.objects.all()}

    today = localdate()
    result = SnapshotResult(seen=0, online=0, traffic_delta=0, unknown_users=0)

    for panel_user in panel_users:
        user_id = by_panel_id.get(panel_user.get("id"))
        if user_id is None:
            result.unknown_users += 1
            continue

        traffic = panel_user.get("userTraffic") or {}
        used = int(traffic.get("usedTrafficBytes") or 0)
        online_at = _as_datetime(traffic.get("onlineAt"))
        node = node_names.get(traffic.get("lastConnectedNodeUuid"), "")

        state = presence.get(user_id)
        # Счётчик в панели может обнулиться при сбросе лимита — тогда прирост
        # считать нельзя, иначе получим огромное отрицательное число.
        delta = max(0, used - state.used_traffic) if state and used >= state.used_traffic else 0

        result.seen += 1
        result.traffic_delta += delta
        if online_at and now() - online_at < timedelta(minutes=5):
            result.online += 1

        with transaction.atomic():
            PanelPresence.objects.update_or_create(
                user_id=user_id,
                defaults={
                    "online_at": online_at,
                    "first_connected_at": _as_datetime(traffic.get("firstConnectedAt")),
                    "node_name": node,
                    "used_traffic": used,
                },
            )
            if node and (delta or not state):
                row, created = NodeUsageDay.objects.get_or_create(
                    user_id=user_id, node_name=node, date=today,
                    defaults={"bytes": delta, "samples": 1},
                )
                if not created:
                    NodeUsageDay.objects.filter(pk=row.pk).update(
                        bytes=F("bytes") + delta, samples=F("samples") + 1
                    )

    result.vanished = _requeue_vanished(panel_users, set(by_panel_id))

    logger.info(
        "Снимок панели: %s пользователей, %s онлайн, +%s Б, вне нашей базы: %s, пропало: %s",
        result.seen, result.online, result.traffic_delta, result.unknown_users, result.vanished,
    )
    return result


def _requeue_vanished(panel_users: list[dict], known_panel_ids: set[int]) -> int:
    """Подписки, чей пользователь исчез из панели, отправить на пересоздание.

    Такое бывает, когда пользователя удалили руками в панели. Подписка при этом
    остаётся в статусе «синхронизирована», а `sync_panel` перебирает только
    несинхронизированные — то есть сама она уже не починится никогда, и человек
    молча остаётся без доступа. Раз мы всё равно забрали полный список, поймаем
    это здесь.
    """
    # Пустой ответ — почти наверняка сбой панели, а не исчезновение всех разом.
    # Сбрасывать по нему статусы нельзя: перевыпустим подписки на пустом месте.
    if not panel_users:
        return 0

    alive = {user.get("id") for user in panel_users}
    lost = Subscription.objects.exclude(panel_user_id=None).exclude(panel_user_id__in=alive)
    lost = lost.filter(panel_user_id__in=known_panel_ids)
    if not lost.exists():
        return 0

    for subscription in lost:
        logger.warning(
            "Пользователь %s пропал из панели (panel_user_id=%s) — ставим на пересоздание",
            subscription.user_id, subscription.panel_user_id,
        )
    return lost.update(panel_user_id=None, panel_status=PanelSyncStatusEnum.PENDING)


def silent_users() -> list[dict]:
    """У кого подписка живая, а трафика нет — кандидаты «у меня не работает».

    Отдельно помечаем тех, кто **никогда** не подключался: у них проблема с
    первым запуском, а не с внезапно отвалившимся туннелем, и разговор с ними
    нужен другой.
    """
    moment = now()
    rows = []
    subscriptions = (
        Subscription.objects.filter(expires_at__gt=moment)
        .exclude(panel_user_id=None)
        .select_related("user", "plan", "user__presence")
    )
    for subscription in subscriptions:
        presence = getattr(subscription.user, "presence", None)
        if presence is None:
            continue
        if presence.online_at and moment - presence.online_at < SILENT_AFTER:
            continue
        rows.append({
            "user": subscription.user,
            "never_connected": presence.first_connected_at is None,
            "last_online": presence.online_at,
            "node": presence.node_name,
            "traffic": presence.used_traffic,
            "expires_at": subscription.expires_at,
        })
    rows.sort(key=lambda row: (not row["never_connected"], row["last_online"] or moment))
    return rows


def usage_by_node(days: int = 7) -> list[dict]:
    """Сводка по нодам за период: сколько людей и сколько байт."""
    since = localdate() - timedelta(days=days - 1)
    # Имена аннотаций не должны совпадать с полями модели: иначе фильтр внутри
    # Count начинает ссылаться на агрегат, а не на колонку, и Postgres ругается
    # «aggregate functions are not allowed in FILTER».
    rows = (
        NodeUsageDay.objects.filter(date__gte=since)
        .values("node_name")
        .annotate(
            total_bytes=Sum("bytes"),
            total_samples=Sum("samples"),
            # Считаем только тех, кто реально что-то прокачал: строка с нулём
            # означает «панель видела его на ноде», а не «он ей пользовался».
            active_users=Count("user_id", distinct=True, filter=Q(bytes__gt=0)),
        )
        .order_by("-total_bytes")
    )
    return [
        {
            "node_name": row["node_name"],
            "bytes": row["total_bytes"] or 0,
            "samples": row["total_samples"] or 0,
            "users": row["active_users"],
        }
        for row in rows
    ]


@dataclass
class IngestResult:
    stored: int
    unknown_users: int


def record_inbound_usage(node_name: str, rows: list[dict]) -> IngestResult:
    """Принять счётчики по туннелям от сборщика на ноде.

    Значения абсолютные (итог за сутки, а не приращение), поэтому пишем их
    как есть: повторная доставка и пропущенный запуск ничего не портят.

    `email` в access log — это числовой id пользователя **в панели**, тот же,
    что лежит в `Subscription.panel_user_id`. Через него и связываем: разбирать
    `tg_<id>` из имени не нужно.
    """
    by_panel_id = dict(
        Subscription.objects.exclude(panel_user_id=None).values_list("panel_user_id", "user_id")
    )
    stored = unknown = 0

    for row in rows:
        user_id = by_panel_id.get(row.get("panel_user_id"))
        if user_id is None:
            unknown += 1
            continue
        InboundUsageDay.objects.update_or_create(
            user_id=user_id,
            node_name=node_name,
            inbound_tag=row["inbound_tag"],
            via_relay=bool(row.get("via_relay")),
            date=row["date"],
            defaults={
                "connections": row.get("connections") or 0,
                "last_seen": _as_datetime(row.get("last_seen")),
            },
        )
        stored += 1

    logger.info("Туннели с ноды %s: записано %s, вне нашей базы: %s", node_name, stored, unknown)
    return IngestResult(stored=stored, unknown_users=unknown)


def usage_by_tunnel(days: int = 7) -> list[dict]:
    """Сводка по туннелям: сколько людей и сколько соединений на каждом.

    Туннель здесь — тройка «нода + инбаунд + пришёл ли через релей». Именно она
    однозначно отвечает строке подписки, которую видит человек: инбаунд один и
    тот же может лежать под двумя разными профилями, отличаясь только тем,
    заходят в него напрямую или через Москву.
    """
    since = localdate() - timedelta(days=days - 1)
    rows = (
        InboundUsageDay.objects.filter(date__gte=since)
        .values("node_name", "inbound_tag", "via_relay")
        .annotate(
            total_connections=Sum("connections"),
            # Только те, кто реально соединялся: нулевую строку сборщик не шлёт,
            # но пусть условие переживёт смену его поведения.
            active_users=Count("user_id", distinct=True, filter=Q(connections__gt=0)),
        )
        .order_by("-total_connections")
    )
    return [
        {
            "node_name": row["node_name"],
            "inbound_tag": row["inbound_tag"],
            "via_relay": row["via_relay"],
            "connections": row["total_connections"] or 0,
            "users": row["active_users"],
        }
        for row in rows
    ]


def tunnels_by_user(days: int = 7, user_ids: list[int] | None = None) -> dict[int, list[dict]]:
    """Кто какими туннелями пользуется — разложено по людям."""
    since = localdate() - timedelta(days=days - 1)
    query = InboundUsageDay.objects.filter(date__gte=since)
    if user_ids is not None:
        query = query.filter(user_id__in=user_ids)

    per_user: dict[int, list[dict]] = {}
    for row in (
        query.values("user_id", "node_name", "inbound_tag", "via_relay")
        .annotate(connections=Sum("connections"), last_seen=Max("last_seen"))
        .order_by("-connections")
    ):
        per_user.setdefault(row["user_id"], []).append(row)
    return per_user


def profile_names(client: RemnawaveClient | None = None) -> dict[tuple[str, str, bool], str]:
    """Как туннель называется для человека в подписке.

    Ключ — та же тройка «нода, инбаунд, через релей»; значение — remark хоста,
    то есть ровно та строка, которую человек видит в приложении. Без этого
    в админке были бы теги вида `VLESS-GRPC`, по которым непонятно, о каком из
    девяти профилей речь.

    Панель — не источник правды для статистики, поэтому её недоступность здесь
    не должна ломать страницу: не смогли — вернём пустой словарь, названия
    отрисуются техническими.
    """
    from django.conf import settings

    client = client or RemnawaveClient()
    try:
        nodes = client.list_nodes()
        hosts = client.list_hosts()
    except Exception as exc:  # панель лежит — страница всё равно должна открыться
        logger.warning("Не удалось получить названия профилей из панели: %s", exc)
        return {}

    relay = settings.RELAY_NODE_ADDRESS
    by_address: dict[tuple[str, str], list[str]] = {}
    tags = {inbound["uuid"]: inbound["tag"] for inbound in client.list_inbounds()}
    for host in hosts:
        tag = tags.get(host.get("inboundUuid"))
        if not tag:
            continue
        by_address.setdefault((host.get("address", ""), tag), []).append(host.get("remark", ""))

    names: dict[tuple[str, str, bool], str] = {}
    for node in nodes:
        node_name = node.get("name", "")
        node_address = node.get("address", "")
        for (address, tag), remarks in by_address.items():
            if address == node_address:
                # Хост указывает на саму ноду — значит, заходят напрямую.
                # Для самого релея это тоже верно: его собственный инбаунд.
                names[(node_name, tag, False)] = " / ".join(sorted(remarks))
            elif address == relay:
                # Хост указывает на релей, а инбаунд лежит здесь: релей
                # пробрасывает соединение сюда, и мы увидим его с его адреса.
                names[(node_name, tag, True)] = " / ".join(sorted(remarks))
    return names


def usage_by_user(days: int = 7, limit: int = 100) -> list[dict]:
    """Кто сколько прокачал и через какие ноды."""
    since = localdate() - timedelta(days=days - 1)
    totals = (
        NodeUsageDay.objects.filter(date__gte=since)
        .values("user_id")
        .annotate(bytes=Sum("bytes"))
        .order_by("-bytes")[:limit]
    )
    user_ids = [row["user_id"] for row in totals]
    users = {u.pk: u for u in NexUser.objects.filter(pk__in=user_ids).select_related("presence")}

    per_node: dict[int, list[tuple[str, int]]] = {}
    for row in (
        NodeUsageDay.objects.filter(date__gte=since, user_id__in=user_ids)
        .values("user_id", "node_name")
        .annotate(bytes=Sum("bytes"))
        .order_by("-bytes")
    ):
        per_node.setdefault(row["user_id"], []).append((row["node_name"], row["bytes"]))

    rows = []
    for row in totals:
        user = users.get(row["user_id"])
        if user is None:
            continue
        presence = getattr(user, "presence", None)
        rows.append({
            "user": user,
            "bytes": row["bytes"],
            "nodes": per_node.get(user.pk, []),
            "online_at": presence.online_at if presence else None,
            "current_node": presence.node_name if presence else "",
        })
    return rows
