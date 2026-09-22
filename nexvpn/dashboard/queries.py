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

from django.db.models import Count, F, Max, Min, Q, Sum
from django.db.models.functions import TruncDay, TruncWeek
from django.utils import timezone

from nexvpn.enums.subscription_event_reason_enum import SubscriptionEventReasonEnum
from nexvpn.subscription import discounts, panel_sync
from nexvpn.models import (
    InboundUsageDay,
    RelayNetworkDay,
    NexUser,
    NodeUsageDay,
    PanelPresence,
    Payment,
    Subscription,
    SubscriptionEvent,
    UserInvitation,
)

from .periods import MAX_BUCKETS, Period

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
    "active_week": "Активные за неделю",
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

    if segment == "active_week":
        # «Активные», которые ещё и реально пользуются: были онлайн за 7 дней.
        # Обычные «Активные» считают и тех, кто подключился однажды и забыл.
        return base.filter(
            subscription__expires_at__gt=moment,
            presence__first_connected_at__isnull=False,
            presence__online_at__gte=moment - dt.timedelta(days=7),
        )

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


def users(segment: str, period: Period, search: str = "", limit: int = 200, discount: str = "") -> list[dict]:
    """Список пользователей сегмента для таблицы.

    `discount` — фильтр по скидке: any / none / pending / id скидки.
    """
    queryset = segment_queryset(segment, period)
    if search:
        queryset = queryset.filter(
            Q(username__icontains=search) | Q(first_name__icontains=search) | Q(id__icontains=search)
        )
    if discount:
        queryset = discounts.holders_filter(queryset, discount)

    # Материализуем до агрегата: срез в подзапросе `user__in` работает не на
    # каждом бэкенде, а список идентификаторов — везде.
    rows = list(queryset.order_by("-created_at")[:limit])

    spent = dict(
        Payment.objects.filter(processed_at__isnull=False, user_id__in=[user.id for user in rows])
        .values_list("user_id")
        .annotate(total=Sum("amount"))
    )
    held = discounts.active_by_user(user.id for user in rows)
    return [_user_row(user, spent.get(user.id, 0), held.get(user.id)) for user in rows]


def discount_options() -> list[dict]:
    """Скидки для фильтра в дашборде: сколько у кого действует сейчас."""
    from nexvpn.models import Discount

    counts = dict(
        discounts.holders_filter(NexUser.objects.all(), "any")
        .values_list("discounts__discount_id")
        .annotate(n=Count("id", distinct=True))
    )
    return [
        {"id": d.pk, "title": d.title, "kind": d.get_kind_display(), "active": counts.get(d.pk, 0)}
        for d in Discount.objects.order_by("title")
    ]


def _discount(held) -> dict | None:
    if held is None:
        return None
    return {
        "title": held.discount.title,
        "via": held.get_via_display(),
        "until": _iso(held.discount.valid_until),
    }


def _user_row(user: NexUser, spent: int = 0, held=None) -> dict:
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
        "discount": _discount(held),
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
    card = _user_row(user, spent, discounts.active_discount(user))
    card["discount_history"] = [
        {
            "title": item.discount.title,
            "status": item.get_status_display(),
            "via": item.get_via_display(),
            "created_at": _iso(item.created_at),
            "decided_at": _iso(item.decided_at),
        }
        for item in user.discounts.select_related("discount").order_by("-created_at")[:20]
    ]

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


# ─────────────────────────────── когорты ───────────────────────────────

# Когорта — неделя, в которую человек появился в базе. Неделя, а не день:
# по дню когорты слишком мелкие, чтобы конверсия в них хоть что-то значила
# (два человека из пяти — это не 40 %, это случайность), а месяц слишком
# грубый, чтобы заметить последствия выката.

# Сколько недель показывать. Меньше четырёх сравнивать не с чем, больше года
# бессмысленно: продукт за год меняется сильнее, чем поведение когорты.
COHORT_WEEK_CHOICES = [4, 8, 12, 26, 52]
DEFAULT_COHORT_WEEKS = 12

# «Недавно был онлайн» для колонки удержания. Неделя, как и в сегменте
# «Активные за неделю», — чтобы две цифры на странице значили одно и то же.
RETENTION_WINDOW = dt.timedelta(days=7)


def cohort_weeks(weeks: int = DEFAULT_COHORT_WEEKS) -> list[dt.date]:
    """Понедельники показываемых недель, от старой к новой.

    Считаем от сегодняшнего локального дня: текущая неделя тоже когорта, просто
    неполная, и прятать её нельзя — именно по ней смотрят, как зашёл выкат.
    """
    weeks = max(1, min(int(weeks), 52))
    today = timezone.localdate()
    monday = today - dt.timedelta(days=today.weekday())
    return [monday - dt.timedelta(days=7 * offset) for offset in range(weeks - 1, -1, -1)]


def cohorts(weeks: int = DEFAULT_COHORT_WEEKS) -> list[dict]:
    """Таблица когорт: новичок за новичком, от свежей недели к старой.

    Три запроса на всю таблицу, а не по запросу на когорту: неделя приклеивается
    к строке прямо в базе (`TruncWeek` по `created_at` пользователя), а по
    Python раскладывается уже готовый агрегат.

    Выручка и удержание считаются за всё время жизни когорты, а не за выбранный
    период: когорта на то и когорта, что её меряют «сколько принесли с тех пор
    как пришли». Поэтому верхний выбор дат на эту таблицу не влияет.
    """
    tz = timezone.get_current_timezone()
    moment = timezone.now()
    mondays = cohort_weeks(weeks)
    start = timezone.make_aware(dt.datetime.combine(mondays[0], dt.time.min), tz)

    basic = {
        _local_date(row["cohort"]): row
        for row in NexUser.objects.filter(created_at__gte=start)
        .annotate(cohort=TruncWeek("created_at", tzinfo=tz))
        .values("cohort")
        .annotate(
            users=Count("id"),
            connected=Count("id", filter=Q(presence__first_connected_at__isnull=False)),
            # «Активен» здесь то же самое, что в сегментах: подписка жива И
            # человек хоть раз подключался. Без второй половины в удержание
            # попадают те, кому дни просто начислили.
            active=Count(
                "id",
                filter=Q(
                    subscription__expires_at__gt=moment,
                    presence__first_connected_at__isnull=False,
                ),
            ),
            online=Count("id", filter=Q(presence__online_at__gte=moment - RETENTION_WINDOW)),
        )
    }

    trials, converted = _cohort_trials(start, tz)
    money = _cohort_money(start, tz)

    rows = []
    for monday in reversed(mondays):
        counts = basic.get(monday, {})
        users = counts.get("users", 0)
        wallet = money.get(monday, {})
        payers = wallet.get("payers", 0)
        revenue = wallet.get("revenue", 0)
        cohort_trials = trials.get(monday, 0)
        paid = converted.get(monday, 0)
        rows.append({
            "week": monday.isoformat(),
            "label": _week_label(monday),
            "users": users,
            "connected": counts.get("connected", 0),
            "connected_share": _share(counts.get("connected", 0), users),
            "trials": cohort_trials,
            "paid": paid,
            # Знаменатель — получившие пробный, а не вся когорта: легаси и те,
            # кто до пробного не дошёл, конверсию не портят, их там и не было.
            "conversion": _share(paid, cohort_trials),
            "payers": payers,
            "payments": wallet.get("payments", 0),
            "revenue": revenue,
            "arppu": round(revenue / payers) if payers else None,
            "avg_check": round(revenue / wallet["payments"]) if wallet.get("payments") else None,
            "days_to_pay": wallet.get("days_to_pay"),
            "repeat_share": _share(wallet.get("repeat", 0), payers),
            "active": counts.get("active", 0),
            "active_share": _share(counts.get("active", 0), users),
            "online": counts.get("online", 0),
            "online_share": _share(counts.get("online", 0), users),
            # Неделя ещё идёт — её цифры заведомо неполные, и фронт это подписывает.
            "partial": monday == mondays[-1],
        })
    return rows


def _cohort_trials(start: dt.datetime, tz) -> tuple[dict[dt.date, int], dict[dt.date, int]]:
    """Сколько в когорте получили пробный и сколько из них потом купили.

    «Потом» — буквально: покупка должна быть позже выдачи пробного. Сравниваем
    с последней покупкой (`Max`), а не с первой: у легаси бывает оплата
    задолго до того, как им выдали пробный при переносе, и по первой покупке
    такой человек выглядел бы как «не конвертировался».
    """
    rows = (
        SubscriptionEvent.objects.filter(
            user__created_at__gte=start,
            reason__in=[SubscriptionEventReasonEnum.TRIAL, SubscriptionEventReasonEnum.PURCHASE],
        )
        .annotate(cohort=TruncWeek("user__created_at", tzinfo=tz))
        .values("user_id", "cohort")
        .annotate(
            trial_at=Min("created_at", filter=Q(reason=SubscriptionEventReasonEnum.TRIAL)),
            purchase_at=Max("created_at", filter=Q(reason=SubscriptionEventReasonEnum.PURCHASE)),
        )
    )
    trials: dict[dt.date, int] = {}
    converted: dict[dt.date, int] = {}
    for row in rows:
        if row["trial_at"] is None:
            continue
        week = _local_date(row["cohort"])
        trials[week] = trials.get(week, 0) + 1
        if row["purchase_at"] is not None and row["purchase_at"] > row["trial_at"]:
            converted[week] = converted.get(week, 0) + 1
    return trials, converted


def _cohort_money(start: dt.datetime, tz) -> dict[dt.date, dict]:
    """Деньги когорты: выручка, платежи, платящие, повторные оплаты, скорость.

    Считаем по успешным платежам (`processed_at`), а не по событиям подписки:
    дни начисляют и промокоды, и админ, а деньги — только оплата.
    """
    rows = (
        Payment.objects.filter(processed_at__isnull=False, user__created_at__gte=start)
        .annotate(cohort=TruncWeek("user__created_at", tzinfo=tz))
        .values("user_id", "cohort", "user__created_at")
        .annotate(total=Sum("amount"), payments=Count("uuid"), first_at=Min("processed_at"))
    )
    money: dict[dt.date, dict] = {}
    lags: dict[dt.date, list[int]] = {}
    for row in rows:
        week = _local_date(row["cohort"])
        wallet = money.setdefault(week, {"revenue": 0, "payments": 0, "payers": 0, "repeat": 0})
        wallet["revenue"] += row["total"] or 0
        wallet["payments"] += row["payments"]
        wallet["payers"] += 1
        if row["payments"] > 1:
            wallet["repeat"] += 1
        lag = (row["first_at"] - row["user__created_at"]).days
        lags.setdefault(week, []).append(max(lag, 0))
    for week, values in lags.items():
        # Медиана, а не среднее: один человек, заплативший через полгода,
        # сдвигает среднее так, что по нему уже ничего не решишь.
        money[week]["days_to_pay"] = _median(values)
    return money


def _week_label(monday: dt.date) -> str:
    """Подпись недели: «14.09 — 20.09». Год не пишем — он виден по порядку строк."""
    return f"{monday:%d.%m} — {monday + dt.timedelta(days=6):%d.%m}"


def _share(part: int, whole: int) -> int | None:
    """Доля в процентах. Нет знаменателя — нет и доли: ноль тут соврал бы."""
    if not whole:
        return None
    return round(100 * part / whole)


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return round((ordered[middle - 1] + ordered[middle]) / 2)


def _local_date(value) -> dt.date:
    """Дата из результата `Trunc`: в базе это datetime в UTC."""
    if isinstance(value, dt.datetime):
        return timezone.localtime(value).date()
    return value


# ─────────────────────── активность по дням ───────────────────────

# Ряды этого раздела всегда по суткам, какая бы детализация ни стояла в шапке.
# «Активных за неделю» нельзя получить сложением семи дневных чисел: один и тот
# же человек попал бы в сумму семь раз. Считать честно по неделям мы умеем, но
# тогда «не подключались больше суток» теряет смысл — метрика-то про сутки.
ACTIVITY_METRICS = {
    "active": {
        "title": "Активных за день",
        "hint": "Сколько разных людей прокачали хоть сколько-то трафика за сутки.",
        "kind": "line",
        "format": "int",
    },
    "new_connected": {
        "title": "Впервые подключились",
        "hint": "Первое в жизни подключение к VPN. Человек дошёл от «завёл подписку» до «работает».",
        "kind": "line",
        "format": "int",
    },
    "traffic": {
        "title": "Трафик за день",
        "hint": "Сумма по всем нодам за сутки.",
        "kind": "bars",
        "format": "bytes",
    },
    "traffic_per_user": {
        "title": "Трафик на активного",
        "hint": "Трафик за сутки, делённый на число активных в этот день. Резкое падение при том же числе людей — признак, что кому-то не работает.",
        "kind": "bars",
        "format": "bytes",
    },
    "silent": {
        "title": "Молчат больше суток",
        "hint": "Были онлайн на прошлой неделе, но за эти сутки не подключались ни разу. Ранний признак, что человек уходит.",
        "kind": "line",
        "format": "int",
    },
}

# Сколько дней подряд считать «вчера ещё пользовался» для «молчунов».
SILENCE_LOOKBACK = 7


def activity_period(period: Period) -> Period:
    """Тот же отрезок, но всегда по суткам и не длиннее, чем влезает в график.

    Длинный кастомный диапазон обрезаем слева, а не укрупняем: «активных за
    день» при укрупнении пришлось бы либо складывать людей по разу за каждый
    день, либо молча подменить метрику. Лучше показать меньше дней честно.
    """
    date_from = max(period.date_from, period.date_to - dt.timedelta(days=MAX_BUCKETS - 1))
    return Period(date_from, period.date_to, "day")


def activity_series(period: Period, metric: str) -> dict:
    """Ряд выбранной метрики по суткам, включая дни без данных."""
    if metric not in ACTIVITY_METRICS:
        metric = "active"
    daily = activity_period(period)
    days = daily.buckets()

    if metric == "new_connected":
        values = _first_connections(daily, days)
    elif metric == "silent":
        values = _silent(daily, days)
    else:
        active = _active_by_day(daily)
        traffic = _traffic_by_day(daily)
        if metric == "active":
            values = [active.get(day, 0) for day in days]
        elif metric == "traffic":
            values = [traffic.get(day, 0) for day in days]
        else:
            # День без единого активного — это не деление на ноль, а просто ноль:
            # трафика в такой день тоже нет.
            values = [
                round(traffic.get(day, 0) / active[day]) if active.get(day) else 0
                for day in days
            ]

    spec = ACTIVITY_METRICS[metric]
    return {
        "metric": metric,
        "title": spec["title"],
        "hint": spec["hint"],
        "kind": spec["kind"],
        "format": spec["format"],
        "labels": [daily.label(day) for day in days],
        "dates": [day.isoformat() for day in days],
        "values": values,
        "from": daily.date_from.isoformat(),
        "to": daily.date_to.isoformat(),
        # Диапазон обрезан слева — фронт обязан сказать об этом вслух.
        "clipped": daily.date_from != period.date_from,
    }


def activity_cards(period: Period) -> list[dict]:
    """Плитки над графиком активности. Первые три — за период, последняя — на сейчас."""
    daily = activity_period(period)
    active = _active_by_day(daily)
    traffic = _traffic_by_day(daily)
    days = daily.buckets()

    unique = (
        NodeUsageDay.objects.filter(date__gte=daily.date_from, date__lte=daily.date_to)
        .values("user_id")
        .distinct()
        .count()
    )
    total_traffic = sum(traffic.values())
    average = round(sum(active.get(day, 0) for day in days) / len(days)) if days else 0

    moment = timezone.now()
    silent_now = NexUser.objects.filter(
        presence__first_connected_at__isnull=False,
        presence__online_at__lt=moment - dt.timedelta(days=1),
        subscription__expires_at__gt=moment,
    ).count()

    return [
        {
            "key": "unique",
            "title": "Пользовались за период",
            "value": unique,
            "format": "int",
            "hint": "Разных людей с трафиком за выбранный период. Человек считается один раз, сколько бы дней ни выходил.",
        },
        {
            "key": "dau",
            "metric": "active",
            "title": "Активных в день",
            "value": average,
            "format": "int",
            "hint": "Среднее число людей с трафиком за сутки по дням периода.",
        },
        {
            "key": "traffic",
            "metric": "traffic",
            "title": "Трафик за период",
            "value": total_traffic,
            "format": "bytes",
            "hint": "Сумма трафика по всем нодам за период.",
        },
        {
            "key": "silent",
            "title": "Не подключались сутки",
            "value": silent_now,
            "format": "int",
            "hint": "Прямо сейчас: подписка жива, VPN когда-то работал, а последний онлайн был больше суток назад.",
        },
    ]


def _active_by_day(period: Period) -> dict[dt.date, int]:
    """Сколько разных людей было с трафиком в каждые сутки."""
    return {
        row["date"]: row["people"]
        for row in NodeUsageDay.objects.filter(
            date__gte=period.date_from, date__lte=period.date_to
        )
        .values("date")
        .annotate(people=Count("user_id", distinct=True))
    }


def _traffic_by_day(period: Period) -> dict[dt.date, int]:
    return {
        row["date"]: row["total"] or 0
        for row in NodeUsageDay.objects.filter(
            date__gte=period.date_from, date__lte=period.date_to
        )
        .values("date")
        .annotate(total=Sum("bytes"))
    }


def _first_connections(period: Period, days: list[dt.date]) -> list[int]:
    """Первые в жизни подключения по суткам — по отметке панели."""
    tz = timezone.get_current_timezone()
    rows = (
        PanelPresence.objects.filter(
            first_connected_at__gte=period.start, first_connected_at__lt=period.end
        )
        .annotate(day=TruncDay("first_connected_at", tzinfo=tz))
        .values("day")
        .annotate(people=Count("user_id"))
    )
    by_day = {_local_date(row["day"]): row["people"] for row in rows}
    return [by_day.get(day, 0) for day in days]


def _silent(period: Period, days: list[dt.date]) -> list[int]:
    """Кто пользовался на прошлой неделе, но за эти сутки не вышел.

    Историю «последнего онлайна» мы не храним: `PanelPresence` перезаписывается,
    и на позапрошлый вторник в нём ничего нет. Зато посуточный трафик хранится —
    по нему молчание и восстанавливается. Цифра на сегодня из плитки над
    графиком и точка ряда за сегодня поэтому чуть разойдутся: плитка знает
    точное время последнего онлайна, ряд — только «были за сутки сообщения».
    """
    pairs = NodeUsageDay.objects.filter(
        date__gte=period.date_from - dt.timedelta(days=SILENCE_LOOKBACK),
        date__lte=period.date_to,
    ).values_list("date", "user_id")

    by_day: dict[dt.date, set[int]] = {}
    for day, user_id in pairs:
        by_day.setdefault(day, set()).add(user_id)

    values = []
    for day in days:
        recent: set[int] = set()
        for back in range(1, SILENCE_LOOKBACK + 1):
            recent |= by_day.get(day - dt.timedelta(days=back), set())
        values.append(len(recent - by_day.get(day, set())))
    return values
