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

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from nexvpn.enums.subscription_event_reason_enum import SubscriptionEventReasonEnum
from nexvpn.models import (
    NexUser,
    NodeUsageDay,
    Payment,
    Subscription,
    SubscriptionEvent,
    UserInvitation,
)

from .periods import Period

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
        "name": user.first_name or "",
        "username": user.username or "",
        "telegram_url": f"https://t.me/{user.username}" if user.username else "",
        "created_at": _iso(user.created_at),
        "is_legacy": user.is_legacy,
        "plan": subscription.plan.name if subscription else "",
        "expires_at": _iso(subscription.expires_at) if subscription else None,
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


def _iso(value) -> str | None:
    """В локальную зону и в ISO — на фронте всё форматируется из этого."""
    if value is None:
        return None
    return timezone.localtime(value).isoformat()
