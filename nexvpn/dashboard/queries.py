"""Все выборки дашборда. Только чтение.

Определения, которые здесь зафиксированы, — те самые, из-за которых дашборд
живёт внутри Django, а не рядом с ним:

* **Пользовался VPN** — `PanelPresence.first_connected_at` не пуст. Панель
  проставляет его при первом реальном подключении, поэтому это честнее, чем
  «есть подписка»: половина заведённых людей не подключалась ни разу.
* **Активный** — подписка ещё не истекла И человек хоть раз подключался.
  Без второй половины в «активных» попадают те, кому дни начислили при
  миграции, а они даже не открыли бота.
* **Ушёл** — подписка истекла, человек ей пользовался под конец
  (`online_at` не раньше чем за неделю до окончания) и не продлил. Это те, кого
  надо смотреть глазами: сервис им был нужен, а потом перестал.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from nexvpn.enums.subscription_event_reason_enum import SubscriptionEventReasonEnum
from nexvpn.subscription import panel_sync
from nexvpn.models import (
    InboundUsageDay,
    RelayNetworkDay,
    NexUser,
    NodeUsageDay,
    Payment,
    Subscription,
    SubscriptionEvent,
    UserInvitation,
)

from .periods import Period

logger = logging.getLogger(__name__)

# Насколько недавно человек должен был выходить в VPN перед окончанием
# подписки, чтобы считаться ушедшим, а не «давно забросил».
CHURN_ACTIVITY_WINDOW = dt.timedelta(days=7)

# Метрики, которые умеет рисовать график. Ключ приходит с фронта.
SERIES_METRICS = {
    "revenue": "Сумма платежей, ₽",
    "payments": "Успешных платежей",
    "registrations": "Новых пользователей",
    "attempts": "Попыток оплаты",
}


# ─────────────────────────────── ряды ───────────────────────────────


def series(period: Period, metric: str) -> dict:
    """Значения метрики по корзинам периода, включая пустые.

    Возвращает готовое к рисованию: подписи, значения, итог.
    """
    if metric not in SERIES_METRICS:
        metric = "revenue"

    if metric == "revenue":
        rows = _grouped(Payment.objects.filter(processed_at__isnull=False), "processed_at", period, Sum("amount"))
    elif metric == "payments":
        rows = _grouped(Payment.objects.filter(processed_at__isnull=False), "processed_at", period, Count("uuid"))
    elif metric == "attempts":
        rows = _grouped(Payment.objects.all(), "created_at", period, Count("uuid"))
    else:
        rows = _grouped(NexUser.objects.all(), "created_at", period, Count("id"))

    buckets = period.buckets()
    values = [rows.get(bucket, 0) for bucket in buckets]
    return {
        "metric": metric,
        "title": SERIES_METRICS[metric],
        "labels": [period.label(bucket) for bucket in buckets],
        "dates": [bucket.isoformat() for bucket in buckets],
        "values": values,
        "total": sum(values),
    }


def _grouped(queryset, field: str, period: Period, aggregate) -> dict[dt.date, int]:
    """Сгруппировать по корзинам периода. Ключ — дата начала корзины."""
    rows = (
        queryset.filter(**{f"{field}__gte": period.start, f"{field}__lt": period.end})
        .annotate(bucket=period.trunc(field))
        .values("bucket")
        .annotate(value=aggregate)
    )
    result: dict[dt.date, int] = {}
    for row in rows:
        bucket = row["bucket"]
        # Trunc возвращает datetime в UTC — переводим обратно в локальную дату,
        # иначе вечерние корзины уедут на сутки вперёд.
        if isinstance(bucket, dt.datetime):
            bucket = timezone.localtime(bucket).date()
        result[bucket] = row["value"] or 0
    return result


# ─────────────────────────────── плитки ───────────────────────────────


def summary(period: Period) -> list[dict]:
    """Плитки над графиком. Те, у которых есть `metric`, кликабельны.

    Часть цифр — за период (выручка, регистрации), часть — состояние на сейчас
    (активные, ушедшие). Смешивать их в одном ряду нормально, но подпись должна
    честно говорить, что есть что, — поэтому у каждой плитки свой `hint`.
    """
    paid = Payment.objects.filter(processed_at__gte=period.start, processed_at__lt=period.end)
    revenue = paid.aggregate(total=Sum("amount"))["total"] or 0
    payments_count = paid.count()
    attempts = Payment.objects.filter(created_at__gte=period.start, created_at__lt=period.end).count()
    registrations = NexUser.objects.filter(created_at__gte=period.start, created_at__lt=period.end).count()

    previous = period.previous()
    previous_revenue = (
        Payment.objects.filter(processed_at__gte=previous.start, processed_at__lt=previous.end)
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )

    trial_total, trial_converted = _trial_conversion(period)

    return [
        {
            "key": "revenue",
            "metric": "revenue",
            "title": "Сумма платежей, ₽",
            "value": revenue,
            "format": "money",
            "hint": "За период. Считается по дате зачисления, сходится с кабинетом ЮKassa.",
            "delta": _delta(revenue, previous_revenue),
        },
        {
            "key": "payments",
            "metric": "payments",
            "title": "Успешных платежей",
            "value": payments_count,
            "format": "int",
            "hint": "За период.",
        },
        {
            "key": "avg_check",
            "title": "Средний чек, ₽",
            "value": round(revenue / payments_count) if payments_count else 0,
            "format": "money",
            "hint": "За период.",
        },
        {
            "key": "registrations",
            "metric": "registrations",
            "title": "Новых пользователей",
            "value": registrations,
            "format": "int",
            "hint": "За период, по дате появления в базе.",
        },
        {
            "key": "attempts",
            "metric": "attempts",
            "title": "Доходит до оплаты",
            "value": round(100 * payments_count / attempts) if attempts else 0,
            "format": "percent",
            "hint": f"{payments_count} оплачено из {attempts} попыток за период.",
        },
        {
            "key": "trial",
            "title": "Пробный → покупка",
            "value": round(100 * trial_converted / trial_total) if trial_total else 0,
            "format": "percent",
            "hint": f"{trial_converted} из {trial_total} получивших пробный за период уже купили.",
        },
        {
            "key": "active",
            "title": "Активных сейчас",
            "value": segment_queryset("active").count(),
            "format": "int",
            "hint": "Подписка не истекла И человек хоть раз подключался к VPN. Не зависит от периода.",
        },
        {
            "key": "churned",
            "title": "Ушли",
            "value": segment_queryset("churned").count(),
            "format": "int",
            "hint": "Пользовались VPN под конец подписки и не продлили. Не зависит от периода.",
        },
    ]


def _delta(current: int, previous: int) -> int | None:
    """Изменение к прошлому такому же периоду, в процентах."""
    if not previous:
        return None
    return round(100 * (current - previous) / previous)


def _trial_conversion(period: Period) -> tuple[int, int]:
    """Сколько людей получили пробный за период и сколько из них купили.

    Считаем по когорте выдачи пробного, а не по дате покупки: иначе в знаменатель
    попадают те, кто пробный только что получил и физически не успел купить.
    """
    trials = SubscriptionEvent.objects.filter(
        reason=SubscriptionEventReasonEnum.TRIAL,
        created_at__gte=period.start,
        created_at__lt=period.end,
    ).values_list("user_id", flat=True)
    trial_users = set(trials)
    if not trial_users:
        return 0, 0
    bought = set(
        SubscriptionEvent.objects.filter(
            reason=SubscriptionEventReasonEnum.PURCHASE, user_id__in=trial_users
        ).values_list("user_id", flat=True)
    )
    return len(trial_users), len(bought)


# ─────────────────────────────── сегменты ───────────────────────────────

SEGMENTS = {
    "active": "Активные",
    "churned": "Ушли",
    "never_connected": "Ни разу не подключались",
    "expiring": "Истекает на неделе",
    "new": "Новые за период",
    "referred": "Пришли по рефералке",
    "trial_open": "На пробном, ещё не купили",
    "all": "Все",
}


def segment_queryset(segment: str, period: Period | None = None):
    """Пользователи одного сегмента. Возвращает queryset, а не список."""
    moment = timezone.now()
    base = NexUser.objects.select_related("subscription", "subscription__plan", "presence")

    if segment == "active":
        return base.filter(subscription__expires_at__gt=moment, presence__first_connected_at__isnull=False)

    if segment == "churned":
        return base.filter(
            subscription__expires_at__lte=moment,
            presence__first_connected_at__isnull=False,
            presence__online_at__gte=F("subscription__expires_at") - CHURN_ACTIVITY_WINDOW,
        )

    if segment == "never_connected":
        return base.filter(presence__first_connected_at__isnull=True)

    if segment == "expiring":
        return base.filter(
            subscription__expires_at__gt=moment,
            subscription__expires_at__lte=moment + dt.timedelta(days=7),
            presence__first_connected_at__isnull=False,
        )

    if segment == "new" and period is not None:
        return base.filter(created_at__gte=period.start, created_at__lt=period.end)

    if segment == "referred":
        return base.filter(invitation__isnull=False)

    if segment == "trial_open":
        trial_users = SubscriptionEvent.objects.filter(
            reason=SubscriptionEventReasonEnum.TRIAL
        ).values_list("user_id", flat=True)
        bought = SubscriptionEvent.objects.filter(
            reason=SubscriptionEventReasonEnum.PURCHASE
        ).values_list("user_id", flat=True)
        return base.filter(id__in=set(trial_users) - set(bought), subscription__expires_at__gt=moment)

    return base


def users(segment: str, period: Period, search: str = "", limit: int = 200) -> list[dict]:
    """Список пользователей сегмента для таблицы."""
    queryset = segment_queryset(segment, period)
    if search:
        queryset = queryset.filter(
            Q(username__icontains=search) | Q(first_name__icontains=search) | Q(id__icontains=search)
        )

    # Материализуем до агрегата: срез в подзапросе `user__in` работает не на
    # каждом бэкенде, а список идентификаторов — везде.
    rows = list(queryset.order_by("-created_at")[:limit])

    spent = dict(
        Payment.objects.filter(processed_at__isnull=False, user_id__in=[user.id for user in rows])
        .values_list("user_id")
        .annotate(total=Sum("amount"))
    )
    return [_user_row(user, spent.get(user.id, 0)) for user in rows]


def _user_row(user: NexUser, spent: int = 0) -> dict:
    subscription = getattr(user, "subscription", None)
    presence = getattr(user, "presence", None)
    return {
        "id": user.id,
        # Вторая дверь к человеку. Ссылки на панель нет и быть не может:
        # у неё один адрес на все экраны, карточка открывается на фронте.
        "admin_url": f"/admin/nexvpn/nexuser/{user.id}/change/",
        "name": user.first_name or "",
        "username": user.username or "",
        "telegram_url": f"https://t.me/{user.username}" if user.username else "",
        "created_at": _iso(user.created_at),
        "is_legacy": user.is_legacy,
        "plan": subscription.plan.name if subscription else "",
        "expires_at": _iso(subscription.expires_at) if subscription else None,
        # Три состояния, а не два. С тех пор как пробный стартует по
        # «Подключиться», а не по /start, «подписки нет вовсе» стало обычным
        # делом: человек открыл бота и остановился. Без этого признака такой
        # человек показывался как «подписка истекла», хотя её и не было.
        "has_subscription": subscription is not None,
        "is_active": bool(subscription and subscription.expires_at > timezone.now()),
        "first_connected_at": _iso(presence.first_connected_at) if presence else None,
        "online_at": _iso(presence.online_at) if presence else None,
        "traffic": presence.used_traffic if presence else 0,
        "spent": spent,
    }


# ─────────────────────────────── платежи ───────────────────────────────


def payments(period: Period, day: str = "", only_paid: bool = True, limit: int = 500) -> list[dict]:
    """Платежи за период. `day` сужает до одной корзины — это провал из графика."""
    queryset = Payment.objects.select_related("user", "plan")

    field = "processed_at" if only_paid else "created_at"
    if only_paid:
        queryset = queryset.filter(processed_at__isnull=False)

    start, end = period.start, period.end
    if day:
        try:
            start, end = period.bucket_range(dt.date.fromisoformat(day))
        except ValueError:
            pass

    queryset = queryset.filter(**{f"{field}__gte": start, f"{field}__lt": end})

    return [
        {
            "uuid": str(payment.uuid),
            "created_at": _iso(payment.created_at),
            "processed_at": _iso(payment.processed_at),
            "amount": payment.amount or 0,
            "kind": payment.get_kind_display() if payment.kind else "",
            "months": payment.period_months,
            "plan": payment.plan.name if payment.plan else "",
            "paid": payment.processed_at is not None,
            "user_id": payment.user_id,
            "user_name": (payment.user.first_name or "") if payment.user else "",
            "username": (payment.user.username or "") if payment.user else "",
        }
        for payment in queryset.order_by(f"-{field}")[:limit]
    ]


# ─────────────────────────── карточка человека ───────────────────────────


def user_card(user_id: int) -> dict | None:
    """Всё, что знаем про одного человека, — чтобы не ходить по вкладкам админки."""
    user = (
        NexUser.objects.select_related("subscription", "subscription__plan", "presence")
        .filter(id=user_id)
        .first()
    )
    if user is None:
        return None

    spent = (
        Payment.objects.filter(user=user, processed_at__isnull=False).aggregate(total=Sum("amount"))["total"] or 0
    )
    card = _user_row(user, spent)

    card["events"] = [
        {
            "created_at": _iso(event.created_at),
            "reason": event.get_reason_display(),
            "days": event.delta_days,
            "amount": event.amount,
            "comment": event.comment,
        }
        for event in SubscriptionEvent.objects.filter(user=user).order_by("-created_at")[:50]
    ]
    card["payments"] = [
        {
            "created_at": _iso(payment.created_at),
            "processed_at": _iso(payment.processed_at),
            "amount": payment.amount or 0,
            "paid": payment.processed_at is not None,
            "plan": payment.plan.name if payment.plan else "",
            "months": payment.period_months,
        }
        for payment in Payment.objects.select_related("plan").filter(user=user).order_by("-created_at")[:50]
    ]
    card["usage"] = [
        {"node": row["node_name"], "bytes": row["total"]}
        for row in NodeUsageDay.objects.filter(user=user)
        .values("node_name")
        .annotate(total=Sum("bytes"))
        .order_by("-total")[:10]
    ]

    card["devices"] = _devices(getattr(user, "subscription", None))

    invitation = UserInvitation.objects.select_related("inviter").filter(invitee=user).first()
    card["invited_by"] = (
        {
            "id": invitation.inviter_id,
            "name": invitation.inviter.first_name or "",
            "username": invitation.inviter.username or "",
        }
        if invitation
        else None
    )
    card["invited_count"] = UserInvitation.objects.filter(inviter=user).count()
    return card


def _devices(subscription) -> dict:
    """Устройства человека — прямо из панели, у нас их нет.

    Мы храним только счётчик первого подключения; какие именно устройства и
    когда были активны, знает панель. Поэтому запрос идёт туда, и только при
    открытии карточки, а не на каждую перерисовку таблицы.

    Панель молчит — говорим об этом прямо. Пустой список и «не смогли
    спросить» это разные вещи: первое значит «устройств нет».
    """
    if subscription is None or not subscription.panel_user_id:
        return {"ok": True, "items": []}
    try:
        raw = panel_sync.list_devices(subscription)
    except Exception as error:
        logger.warning("Панель не отдала устройства для %s: %s", subscription.user_id, error)
        return {"ok": False, "items": []}

    items = [
        {
            "title": device.get("deviceModel") or device.get("platform") or "Без названия",
            "platform": device.get("platform") or "",
            "os": device.get("osVersion") or "",
            "app": (device.get("userAgent") or "").split("/")[0],
            "hwid": (device.get("hwid") or "")[:8],
            # updatedAt — последняя активность, createdAt — когда устройство
            # впервые появилось. Панель отдаёт время в UTC со сдвигом Z.
            "last_seen": device.get("updatedAt"),
            "first_seen": device.get("createdAt"),
        }
        for device in raw
    ]
    items.sort(key=lambda item: item["last_seen"] or "", reverse=True)
    return {"ok": True, "items": items}


def _iso(value) -> str | None:
    """В локальную зону и в ISO — на фронте всё форматируется из этого."""
    if value is None:
        return None
    return timezone.localtime(value).isoformat()


# ─────────────────────────────── туннели ───────────────────────────────

# Названия профилей берутся из панели. Ходить туда на каждый клик по периоду
# незачем: список меняется раз в неделю, а панель за Cloudflare и отвечает не
# мгновенно. Держим недолгий кэш в памяти процесса.
_PROFILE_CACHE: dict[str, object] = {"names": {}, "at": None}
_PROFILE_TTL = dt.timedelta(minutes=10)


def profile_names() -> dict[tuple[str, str, bool], str]:
    """Как туннель называется для человека. Пустой словарь — не беда."""
    from nexvpn import telemetry

    fetched_at = _PROFILE_CACHE["at"]
    if fetched_at is None or timezone.now() - fetched_at > _PROFILE_TTL:
        try:
            _PROFILE_CACHE["names"] = telemetry.profile_names()
        except Exception:
            # Панель недоступна — покажем технические теги. Страница со
            # статистикой не должна зависеть от чужой доступности.
            _PROFILE_CACHE["names"] = _PROFILE_CACHE["names"] or {}
        _PROFILE_CACHE["at"] = timezone.now()
    return _PROFILE_CACHE["names"]


def _tunnel_title(names: dict, node: str, tag: str, via_relay: bool) -> str:
    name = names.get((node, tag, via_relay))
    if name:
        return name
    # Профиль мог быть удалён из подписки, а трафик по нему ещё идёт: у людей
    # в приложениях остаются старые конфиги. Такие показываем тегом и метим.
    return f"{tag} @ {node}{' через релей' if via_relay else ''}"


def tunnels(period: Period) -> list[dict]:
    """Туннели за период: сколько людей, сколько соединений, с каких сетей."""
    names = profile_names()
    rows = (
        InboundUsageDay.objects
        .filter(date__gte=period.date_from, date__lte=period.date_to)
        .values("node_name", "inbound_tag", "via_relay")
        .annotate(
            connections=Sum("connections"),
            people=Count("user_id", distinct=True),
            mobile=Count("user_id", distinct=True, filter=Q(network=InboundUsageDay.Network.MOBILE)),
            fixed=Count("user_id", distinct=True, filter=Q(network=InboundUsageDay.Network.FIXED)),
        )
        .order_by("-people", "-connections")
    )
    relayed = {
        (row["node"], row["tag"]): row for row in relay_networks(period)
    }
    result = []
    for row in rows:
        key = (row["node_name"], row["inbound_tag"])
        # У релейного туннеля людей мы знаем (их видит выходная нода), а сеть —
        # только в адресах, и приходит она с самого релея.
        extra = relayed.get(key, {}) if row["via_relay"] else {}
        result.append({
            "title": _tunnel_title(names, row["node_name"], row["inbound_tag"], row["via_relay"]),
            "known": (row["node_name"], row["inbound_tag"], row["via_relay"]) in names,
            "node": row["node_name"],
            "tag": row["inbound_tag"],
            "via_relay": row["via_relay"],
            "people": row["people"],
            "connections": row["connections"] or 0,
            "mobile": extra.get("mobile", row["mobile"]),
            "fixed": extra.get("fixed", row["fixed"]),
            # Для релейных цифры рядом — это адреса, а не люди. Фронт подписывает.
            "network_in_clients": bool(extra),
        })
    return result


def operators(period: Period, limit: int = 25) -> list[dict]:
    """С каких сетей к нам приходят.

    Две цифры рядом, а не одна сумма: по прямым туннелям мы считаем **людей**
    (в логе есть и адрес, и кто это), по релейным — только **адреса** (релей
    видит адрес, но не знает, кто за ним). Складывать их нельзя: за одним
    адресом бывает несколько человек, а один человек на мобильном за сутки
    меняет адрес не раз. Поэтому столбцы разные и подписаны по-разному.
    """
    direct = {
        row["asn"]: row
        for row in InboundUsageDay.objects
        .filter(date__gte=period.date_from, date__lte=period.date_to)
        .exclude(asn=0)
        .values("asn", "operator", "network")
        .annotate(connections=Sum("connections"), people=Count("user_id", distinct=True))
    }
    relayed = {
        row["asn"]: row
        for row in RelayNetworkDay.objects
        .filter(date__gte=period.date_from, date__lte=period.date_to)
        .exclude(asn=0)
        .values("asn", "operator", "network")
        .annotate(connections=Sum("connections"), clients=Sum("clients"))
    }

    merged: list[dict] = []
    for asn in set(direct) | set(relayed):
        here, there = direct.get(asn, {}), relayed.get(asn, {})
        merged.append({
            "asn": asn,
            "operator": here.get("operator") or there.get("operator") or f"AS{asn}",
            "network": here.get("network") or there.get("network") or "unknown",
            "people": here.get("people", 0),
            "relay_clients": there.get("clients", 0),
            "connections": (here.get("connections") or 0) + (there.get("connections") or 0),
        })
    merged.sort(key=lambda row: (-row["people"], -row["relay_clients"], -row["connections"]))
    return merged[:limit]


def relay_networks(period: Period) -> list[dict]:
    """Разбивка релейных туннелей по типу сети — в адресах, не в людях."""
    rows = (
        RelayNetworkDay.objects
        .filter(date__gte=period.date_from, date__lte=period.date_to)
        .values("inbound_tag", "node_name", "network")
        .annotate(clients=Sum("clients"), connections=Sum("connections"))
    )
    by_tunnel: dict[tuple, dict] = {}
    for row in rows:
        key = (row["node_name"], row["inbound_tag"])
        target = by_tunnel.setdefault(key, {"mobile": 0, "fixed": 0, "unknown": 0})
        target[row["network"]] = target.get(row["network"], 0) + (row["clients"] or 0)
    return [{"node": node, "tag": tag, **counts} for (node, tag), counts in by_tunnel.items()]


def networks(period: Period) -> list[dict]:
    """Мобильные, домашние и те, у кого сеть не видна.

    Третья группа — это не сбой: у релейных туннелей адрес московский, и
    оператор человека из него не выводится. На странице так и подписано.
    """
    titles = {
        InboundUsageDay.Network.MOBILE: "Мобильный интернет",
        InboundUsageDay.Network.FIXED: "Домашний интернет",
        InboundUsageDay.Network.UNKNOWN: "Сеть не видна (через релей)",
    }
    rows = (
        InboundUsageDay.objects
        .filter(date__gte=period.date_from, date__lte=period.date_to)
        .values("network")
        .annotate(connections=Sum("connections"), people=Count("user_id", distinct=True))
    )
    by_kind = {row["network"]: row for row in rows}
    return [
        {
            "kind": kind,
            "title": title,
            "people": by_kind.get(kind, {}).get("people", 0),
            "connections": by_kind.get(kind, {}).get("connections") or 0,
        }
        for kind, title in titles.items()
    ]


def tunnel_series(period: Period, metric: str = "people") -> dict:
    """Динамика по суткам: сколько людей (или соединений) на каждом туннеле.

    Ряд на туннель, а не одна линия: вопрос «какой туннель сдаёт» без разбивки
    не читается — общая сумма может стоять на месте, пока люди перетекают с
    одного входа на другой.
    """
    names = profile_names()
    aggregate = Count("user_id", distinct=True) if metric == "people" else Sum("connections")
    rows = (
        InboundUsageDay.objects
        .filter(date__gte=period.date_from, date__lte=period.date_to)
        .values("date", "node_name", "inbound_tag", "via_relay")
        .annotate(value=aggregate)
    )

    buckets = period.buckets()
    index = {bucket: position for position, bucket in enumerate(buckets)}
    lines: dict[tuple, list[int]] = {}
    for row in rows:
        key = (row["node_name"], row["inbound_tag"], row["via_relay"])
        # Сутки могут не попасть в корзину, если период укрупнён до недель.
        bucket = period._floor(row["date"])
        position = index.get(bucket)
        if position is None:
            continue
        line = lines.setdefault(key, [0] * len(buckets))
        line[position] += row["value"] or 0

    series = [
        {"title": _tunnel_title(names, *key), "values": values, "total": sum(values)}
        for key, values in lines.items()
    ]
    series.sort(key=lambda item: -item["total"])
    return {
        "labels": [period.label(bucket) for bucket in buckets],
        "dates": [bucket.isoformat() for bucket in buckets],
        "series": series[:10],
        "metric": metric,
    }


def operator_tunnel_matrix(period: Period, limit: int = 12) -> dict:
    """Кто каким туннелем пользуется в разрезе «оператор × туннель».

    Ради этого всё и затевалось: две отдельные таблицы — «людей на туннеле» и
    «людей у оператора» — по отдельности не отвечают на вопрос «что не работает
    на Tele2». Отвечает только пересечение.

    Считаются **люди**, поэтому строка не суммируется в итог оператора: один
    человек за сутки ходит через несколько туннелей и попадёт в несколько
    ячеек. Пустая ячейка означает «никто с этой сети сюда не заходил» — а вот
    почему, из лога не видно: не выбрали или не работает.

    Релейные туннели сюда не попадают: у них адрес московский, оператор
    человека не определяется. Появятся, когда доедет статистика с релея.
    """
    names = profile_names()
    rows = (
        InboundUsageDay.objects
        .filter(date__gte=period.date_from, date__lte=period.date_to)
        .exclude(asn=0)
        .values("asn", "operator", "network", "node_name", "inbound_tag", "via_relay")
        .annotate(people=Count("user_id", distinct=True), connections=Sum("connections"))
    )

    tunnels_seen: dict[tuple, int] = {}
    operators_seen: dict[int, dict] = {}
    cells: dict[tuple, dict] = {}
    for row in rows:
        tunnel = (row["node_name"], row["inbound_tag"], row["via_relay"])
        tunnels_seen[tunnel] = tunnels_seen.get(tunnel, 0) + row["people"]
        operator = operators_seen.setdefault(row["asn"], {
            "asn": row["asn"],
            "operator": row["operator"] or f"AS{row['asn']}",
            "network": row["network"],
            "people": 0,
        })
        operator["people"] += row["people"]
        cells[(row["asn"], tunnel)] = {
            "people": row["people"],
            "connections": row["connections"] or 0,
        }

    # Порядок: и туннели, и операторы — по числу людей, чтобы главное было
    # в левом верхнем углу, а хвост не мешал читать.
    tunnel_order = sorted(tunnels_seen, key=lambda key: -tunnels_seen[key])
    operator_order = sorted(operators_seen.values(), key=lambda row: -row["people"])[:limit]

    return {
        "tunnels": [
            {"title": _tunnel_title(names, *tunnel), "key": "|".join(map(str, tunnel))}
            for tunnel in tunnel_order
        ],
        "rows": [
            {
                **operator,
                "cells": [
                    cells.get((operator["asn"], tunnel), {}).get("people", 0)
                    for tunnel in tunnel_order
                ],
            }
            for operator in operator_order
        ],
    }
