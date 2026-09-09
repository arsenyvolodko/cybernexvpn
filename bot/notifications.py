"""Напоминания об окончании подписки.

Задача крутится часто, а окно «осталось меньше N часов» держится долго —
поэтому факт отправки фиксируется в `SentReminder`, и человек получает каждое
напоминание ровно один раз. При продлении записи сбрасываются: начался новый
период, значит и напоминать по нему надо заново (см. `service.grant_days`).

Ночные пуши разруливаются не здесь, а нормализацией времени окончания:
`SUBSCRIPTION_EXPIRY_HOUR` подобран так, чтобы все смещения попадали в день.
"""

import logging
from datetime import timedelta

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from django.conf import settings
from django.db import IntegrityError
from django.utils.timezone import now

from bot import texts
from bot.keyboards import keyboards
from nexvpn.enums import SubscriptionEventReasonEnum
from nexvpn.models import SentReminder, Subscription, SubscriptionEvent
from nexvpn.subscription import panel_sync

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


# Смещение, под которым в `SentReminder` лежит отметка «сказали, что кончилась».
# Ноль потому, что это не «за сколько-то до», а сам момент окончания. Отметка
# живёт в той же таблице не для экономии: `grant_days` чистит её при продлении,
# и уведомление об окончании автоматически становится возможным снова.
EXPIRY_OFFSET = 0

# Насколько назад смотрим. Задача ходит раз в десять минут, поэтому в обычной
# жизни сообщение уходит почти сразу — окно нужно только чтобы догнать
# пропущенное после простоя. Двенадцати часов на это хватает, а написать
# «подписка приостановлена» тому, у кого это случилось позавчера, уже поздно.
#
# Обратная сторона: если бот пролежит дольше двенадцати часов, тех, кто истёк
# в это время, мы пропустим совсем.
EXPIRY_WINDOW = timedelta(hours=12)


def due_expiry_notices() -> list[Subscription]:
    """Кому пора сказать, что подписка кончилась.

    Только тем, у кого это случилось недавно и кому мы ещё не говорили.
    Повторно не скажем никогда: отметка снимается лишь продлением, а после
    продления сообщение снова уместно — но уже про новый период.
    """
    moment = now()
    subscriptions = (
        Subscription.objects
        .filter(expires_at__lte=moment, expires_at__gt=moment - EXPIRY_WINDOW)
        .select_related("user", "plan")
    )
    already_told = set(
        SentReminder.objects
        .filter(hours_before=EXPIRY_OFFSET)
        .values_list("subscription_id", flat=True)
    )
    return [s for s in subscriptions if s.pk not in already_told]


async def send_due_expiry_notices(bot) -> tuple[int, int]:
    """Разослать сообщения об окончании. Возвращает (отправлено, не доставлено)."""
    import asyncio

    from asgiref.sync import sync_to_async

    pending = await sync_to_async(due_expiry_notices)()
    if not pending:
        return 0, 0

    sent = failed = 0
    for subscription in pending:
        # Отметку ставим до отправки, как и у напоминаний: повторить
        # пропущенное не страшно, а сказать дважды «подписка закончилась» —
        # заметно и неприятно.
        try:
            await sync_to_async(SentReminder.objects.create)(
                subscription=subscription,
                hours_before=EXPIRY_OFFSET,
                expires_at=subscription.expires_at,
            )
        except IntegrityError:
            continue

        if await _send(bot, subscription.user_id, texts.SUBSCRIPTION_ENDED, keyboards.ended()):
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(DELAY_BETWEEN_MESSAGES)

    return sent, failed


def _timedelta_hours(hours: int):
    return timedelta(hours=hours)


# Ниже этого порога говорим в часах, выше — в днях. Порог не ровно сутки:
# при 23 ч 59 мин честнее сказать «1 день», чем «24 часа».
DAYS_THRESHOLD_MINUTES = 23.5 * 60


def remaining(subscription: Subscription) -> str:
    """Сколько осталось, словами. Округление — к ближайшему.

    Раньше остаток обрезался вниз, и это давало сразу две ошибки. Напоминание
    за два часа уходило, когда до конца оставалось 1 ч 58 мин, обрезалось до
    единицы и говорило «1 час» — на час меньше правды. А следующее, настоящее
    часовое напоминание говорило ровно то же самое, и человек дважды подряд
    читал одно и то же.

    Округление вниз задумывалось как «не обещать больше, чем есть». Но
    ошибалось оно не в пользу человека, а против: пугало концом раньше срока и
    делало два разных напоминания неразличимыми. К ближайшему — и текст
    совпадает с тем смещением, ради которого напоминание отправлено.
    """
    minutes_left = (subscription.expires_at - now()).total_seconds() / 60
    if minutes_left >= DAYS_THRESHOLD_MINUTES:
        return texts.plural_days(round(minutes_left / (24 * 60)))
    # Не меньше часа: «осталось 0 часов» человеку ничего не говорит, а
    # напоминание за час приходит, когда остаётся чуть меньше часа.
    return texts.plural_hours(max(1, round(minutes_left / 60)))


# До какого смещения предупреждать про лишние устройства. Дальше суток это
# шум: до перехода ещё далеко, человек успеет забыть. За сутки и ближе — это
# последний момент, когда он может выбрать сам, а не узнать постфактум.
TRIM_NOTICE_MAX_HOURS = 24


def trim_notice(subscription: Subscription) -> str:
    """Приписка про лишние устройства, если на носу переход на меньший тариф.

    Пустая строка, если перехода нет, устройств не больше лимита или панель
    не ответила: пугать человека догадкой нельзя.
    """
    if subscription.next_plan_id is None:
        return ""
    try:
        used = len(panel_sync.list_devices(subscription))
    except Exception:
        logger.warning("Панель не отдала устройства для напоминания %s", subscription.pk)
        return ""
    limit = subscription.next_plan.device_limit
    if used <= limit:
        return ""
    return texts.REMINDER_TRIM_NOTICE.format(
        plan=texts.plural_devices(limit),
        used=texts.plural_devices(used),
        limit=texts.plural_devices(limit),
    )


def build_text(subscription: Subscription) -> str:
    """Текст напоминания.

    Остаток считаем по реальному времени до конца, а не по номиналу
    сработавшего смещения. Смещение — только повод отправить: после продления
    ближайшим сработавшим оказывается самое дальнее из них, и человек с двумя
    сутками в запасе получал «осталось 7 дней» рядом с верной датой.

    Абсолютной даты в тексте нет: окончание округляется к 20:00 по Москве, а
    часовой пояс человека Telegram нам не сообщает.
    """
    return texts.REMINDER.format(
        left=remaining(subscription),
        plan=texts.plural_devices(subscription.plan.device_limit),
    )


def _reminder_extras(subscription: Subscription, hours_before: int) -> tuple[str, bool]:
    """Приписка и нужна ли кнопка «Мои устройства». Только за сутки и ближе."""
    if hours_before > TRIM_NOTICE_MAX_HOURS:
        return "", False
    notice = trim_notice(subscription)
    return notice, bool(notice)


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
            notice, with_devices = _reminder_extras(subscription, hours_before)
            text += notice
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

        if await _send(bot, subscription.user_id, text, keyboards.reminder(with_devices=with_devices)):
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(DELAY_BETWEEN_MESSAGES)

    return sent, failed


async def _send(bot, chat_id: int, text: str, keyboard) -> bool:
    import asyncio

    try:
        await bot.send_message(chat_id, text, reply_markup=keyboard)
        return True
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after + 1)
        try:
            await bot.send_message(chat_id, text, reply_markup=keyboard)
            return True
        except TelegramAPIError:
            return False
    except TelegramAPIError as exc:
        # Заблокировал бота или удалил чат — это норма, не ошибка.
        logger.debug("Не доставили напоминание %s: %s", chat_id, exc)
        return False
