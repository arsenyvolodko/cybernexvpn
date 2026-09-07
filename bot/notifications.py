"""Напоминания об окончании подписки.

Задача крутится часто, а окно «осталось меньше N часов» держится долго —
поэтому факт отправки фиксируется в `SentReminder`, и человек получает каждое
напоминание ровно один раз. При продлении записи сбрасываются: начался новый
период, значит и напоминать по нему надо заново (см. `service.grant_days`).

Ночные пуши разруливаются не здесь, а нормализацией времени окончания:
`SUBSCRIPTION_EXPIRY_HOUR` подобран так, чтобы все смещения попадали в день.
"""

import logging

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from django.conf import settings
from django.db import IntegrityError
from django.utils.timezone import localtime, now

from bot import texts
from bot.keyboards import keyboards
from nexvpn.enums import SubscriptionEventReasonEnum
from nexvpn.models import SentReminder, Subscription, SubscriptionEvent

logger = logging.getLogger(__name__)

DELAY_BETWEEN_MESSAGES = 0.05


def due_reminders() -> list[tuple[Subscription, int]]:
    """Кому и какое напоминание пора отправить.

    Для каждой подписки берём **самое близкое** сработавшее смещение: если
    задача не отработала полдня, человек получит «остался час», а не пачку из
    четырёх сообщений подряд.
    """
    offsets = sorted(set(settings.SUBSCRIPTION_REMINDER_HOURS))
    if not offsets:
        return []

    moment = now()
    horizon = moment + _timedelta_hours(max(offsets))

    subscriptions = list(
        Subscription.objects
        .filter(expires_at__gt=moment, expires_at__lte=horizon)
        .select_related("user", "plan")
    )

    already_sent = {
        (subscription_id, hours)
        for subscription_id, hours in SentReminder.objects.values_list("subscription_id", "hours_before")
    }
    trial_subscriptions = _trial_subscription_ids(subscriptions)

    result: list[tuple[Subscription, int]] = []
    for subscription in subscriptions:
        hours_left = (subscription.expires_at - moment).total_seconds() / 3600
        applicable = _offsets_for(subscription, offsets, trial_subscriptions)
        candidates = [offset for offset in applicable if hours_left <= offset]
        if not candidates:
            continue
        # Самое близкое смещение — оно точнее описывает текущий момент.
        closest = min(candidates)
        if (subscription.pk, closest) in already_sent:
            continue
        result.append((subscription, closest))
    return result


def _offsets_for(subscription: Subscription, offsets: list[int], trial_ids: set[int]) -> list[int]:
    """Какие смещения вообще применимы к этой подписке.

    На пробном периоде смещения длиннее `TRIAL_REMINDER_MAX_HOURS` отбрасываются.
    Причина в том, что смещение считается сработавшим, когда до конца осталось
    меньше него, — а у трёхдневного пробного «за неделю» выполняется с первой
    секунды. Без этого фильтра человек получал «Подписка заканчивается» через
    минуту после регистрации, и это была не редкость, а поведение по умолчанию.
    """
    if subscription.pk not in trial_ids:
        return offsets
    return [offset for offset in offsets if offset <= settings.TRIAL_REMINDER_MAX_HOURS]


def _trial_subscription_ids(subscriptions: list[Subscription]) -> set[int]:
    """Подписки, текущий период которых выдан как пробный.

    Смотрим на **последнее** событие: пробный, за которым последовала покупка,
    пробным больше не считается — там уже оплаченный период обычной длины.
    """
    if not subscriptions:
        return set()

    latest_reason: dict[int, str] = {}
    rows = (
        SubscriptionEvent.objects
        .filter(subscription_id__in=[subscription.pk for subscription in subscriptions])
        .order_by("subscription_id", "-created_at", "-id")
        .values_list("subscription_id", "reason")
    )
    for subscription_id, reason in rows:
        latest_reason.setdefault(subscription_id, reason)

    return {
        subscription_id
        for subscription_id, reason in latest_reason.items()
        if reason == SubscriptionEventReasonEnum.TRIAL
    }


def _timedelta_hours(hours: int):
    from datetime import timedelta

    return timedelta(hours=hours)


def build_text(subscription: Subscription) -> str:
    """Текст напоминания.

    «Осталось» считаем по реальному времени до конца, а не по номиналу
    сработавшего смещения. Смещение — только повод отправить: после продления
    ближайшим сработавшим оказывается самое дальнее из них, и человек с двумя
    сутками в запасе получал «осталось 7 дней» рядом с верной датой.

    Округляем вниз: обещать времени больше, чем есть, в напоминании об
    окончании — ровно та ошибка, от которой оно должно защищать.
    """
    plan_title = texts.plural_devices(subscription.plan.device_limit)
    hours_left = (subscription.expires_at - now()).total_seconds() / 3600
    if hours_left >= 24:
        left = texts.plural_days(int(hours_left // 24))
    else:
        left = texts.plural_hours(max(1, int(hours_left)))
    return texts.REMINDER.format(
        left=left,
        # Время локальное: в базе оно в UTC, и без перевода человек читал бы
        # «до 20:00» как «до 17:00».
        until=localtime(subscription.expires_at).strftime("%d.%m в %H:%M"),
        plan=plan_title,
    )


async def send_due_reminders(bot) -> tuple[int, int]:
    """Разослать всё, что назрело. Возвращает (отправлено, не доставлено)."""
    import asyncio

    from asgiref.sync import sync_to_async

    pending = await sync_to_async(due_reminders)()
    if not pending:
        return 0, 0

    sent = failed = 0
    for subscription, hours_before in pending:
        # Текст собираем до отметки об отправке. Если он не собрался, отметки
        # быть не должно: иначе сбой не только не отправит напоминание, но и
        # навсегда закроет это смещение для человека.
        try:
            text = build_text(subscription)
        except Exception:
            # Один человек не должен уносить с собой всю пачку: дальше по списку
            # стоят те, кому напоминание ещё можно доставить вовремя.
            logger.exception("Не собрали напоминание для подписки %s", subscription.pk)
            failed += 1
            continue

        # Отметку ставим до отправки: повторить пропущенное напоминание не
        # страшно, а отправить одно и то же дважды — заметно и неприятно.
        try:
            await sync_to_async(SentReminder.objects.create)(
                subscription=subscription,
                hours_before=hours_before,
                expires_at=subscription.expires_at,
            )
        except IntegrityError:
            continue

        if await _send(bot, subscription.user_id, text):
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(DELAY_BETWEEN_MESSAGES)

    return sent, failed


async def _send(bot, chat_id: int, text: str) -> bool:
    import asyncio

    try:
        await bot.send_message(chat_id, text, reply_markup=keyboards.reminder())
        return True
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after + 1)
        try:
            await bot.send_message(chat_id, text, reply_markup=keyboards.reminder())
            return True
        except TelegramAPIError:
            return False
    except TelegramAPIError as exc:
        # Заблокировал бота или удалил чат — это норма, не ошибка.
        logger.debug("Не доставили напоминание %s: %s", chat_id, exc)
        return False
