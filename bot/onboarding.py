"""Подталкивания новичку, который открыл бота и не довёл дело до устройства.

Четыре сообщения — через 10 минут, час, 3 часа и сутки после первого захода, —
и каждое отменяется, как только появилось хоть одно устройство.

Две вещи здесь сделаны намеренно и ломаются, если их упростить.

**Отсечка по времени выката** (`ONBOARDING_NUDGES_SINCE`). Работает вместе с
окном и закрывает то, что окно не закрывает. Кто заходил давно (на 11.09.2026
таких 204) выпадает сам: окно держит человека в поле зрения только сутки с
хвостиком. А вот открывший бота за пару часов до выката в окно попадает — и
без отсечки получил бы подталкивание задним числом. Таких на момент включения
было двое, но именно из-за них отсечка и нужна.

**Панель дёргаем только в момент, когда шаг уже пора отправить.** Список
кандидатов считается одним запросом в базу, и у большинства из них устройств
быть не может в принципе — подписки ещё нет, а значит нет и пользователя в
панели. Так проверка стоит один дешёвый SQL раз в пару минут, а не обход всех
подписок по API.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils.timezone import now

from bot import texts
from bot.keyboards import keyboards
from nexvpn.models import NexUser, OnboardingNudge, Subscription
from nexvpn.subscription import panel_sync

logger = logging.getLogger(__name__)

# Через сколько минут после первого захода и что отправляем.
NUDGES: list[tuple[int, str]] = [
    (10, texts.ONBOARDING_NUDGE_10M),
    (60, texts.ONBOARDING_NUDGE_1H),
    (180, texts.ONBOARDING_NUDGE_3H),
    (24 * 60, texts.ONBOARDING_NUDGE_1D),
]

# Сколько ещё держим человека в поле зрения после последнего шага. Нужен запас
# на случай, если задача не работала ровно в тот момент, когда шаг стал нужен.
WINDOW_SLACK = timedelta(hours=2)


def _cutoff():
    """С какого момента подталкиваем. None — фича выключена."""
    raw = (settings.ONBOARDING_NUDGES_SINCE or "").strip()
    if not raw:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        logger.error("ONBOARDING_NUDGES_SINCE не разобрать (%r) — подталкивания выключены", raw)
        return None


def due_nudges() -> list[tuple[NexUser, list[tuple[int, str]]]]:
    """Кому какие шаги уже просрочены. Только запрос в базу, без похода в панель."""
    cutoff = _cutoff()
    if cutoff is None:
        return []

    moment = now()
    window_start = moment - timedelta(minutes=NUDGES[-1][0]) - WINDOW_SLACK
    first_step = timedelta(minutes=NUDGES[0][0])

    candidates = NexUser.objects.filter(
        # Легаси не трогаем: у них подписка уже оплачена, и тексты про
        # «опробовать сервис» обращены не к ним.
        is_legacy=False,
        activated_at__gte=max(cutoff, window_start),
        activated_at__lte=moment - first_step,
    )

    pending = []
    for user in candidates:
        closed = set(
            OnboardingNudge.objects.filter(user=user).values_list("step_minutes", flat=True)
        )
        due = [
            (minutes, text)
            for minutes, text in NUDGES
            if minutes not in closed and user.activated_at + timedelta(minutes=minutes) <= moment
        ]
        if due:
            pending.append((user, due))
    return pending


def has_device(user: NexUser) -> bool | None:
    """Есть ли хоть одно устройство. None — панель не ответила.

    Подписки нет — значит нет и пользователя в панели, а с ним и устройств:
    такой ответ даём сразу, не трогая сеть. Это и есть тот случай, ради
    которого проверка остаётся дешёвой, — у большинства новичков в первые
    минуты подписки ещё нет.
    """
    subscription = Subscription.objects.filter(user=user).first()
    if subscription is None or subscription.panel_user_id is None:
        return False
    try:
        return bool(panel_sync.list_devices(subscription))
    except Exception:
        logger.warning("Панель не ответила про устройства %s", user.pk, exc_info=True)
        return None


def _suppress(user: NexUser, steps: list[int]) -> None:
    """Закрыть шаги без отправки. Гонок тут не боимся: итог один и тот же."""
    OnboardingNudge.objects.bulk_create(
        [OnboardingNudge(user=user, step_minutes=step, sent=False) for step in steps],
        ignore_conflicts=True,
    )


def _claim(user: NexUser, step: int) -> bool:
    """Занять шаг под отправку. False — кто-то опередил.

    `atomic` здесь не украшение: в Postgres упавший запрос рвёт всю текущую
    транзакцию, и пойманный IntegrityError без своей точки отката оставил бы
    нас с непригодным соединением до конца прохода.
    """
    try:
        with transaction.atomic():
            OnboardingNudge.objects.create(user=user, step_minutes=step, sent=True)
    except IntegrityError:
        return False
    return True


async def send_due_nudges(bot) -> tuple[int, int]:
    """Разослать подталкивания. Возвращает (отправлено, не доставлено)."""
    from asgiref.sync import sync_to_async

    from bot.notifications import _send

    pending = await sync_to_async(due_nudges)()
    if not pending:
        return 0, 0

    sent = failed = 0
    for user, due in pending:
        state = await sync_to_async(has_device)(user)
        if state is None:
            # Панель молчит — промолчим и мы. Шаг не закрываем: вернёмся к
            # нему в следующий проход, когда будет что утверждать наверняка.
            continue
        if state:
            # Устройство появилось — подталкивать больше не о чем. Закрываем
            # всё разом, чтобы человек не попадал в выборку до конца суток.
            await sync_to_async(_suppress)(user, [m for m, _ in NUDGES])
            continue

        step, text = due[-1]
        stale = [minutes for minutes, _ in due[:-1]]
        if stale:
            # Задача не работала несколько часов. Отправляем только самое
            # свежее: четыре сообщения подряд выглядели бы как поломка.
            await sync_to_async(_suppress)(user, stale)

        # Отметку ставим до отправки, как и у напоминаний об окончании:
        # пропустить подталкивание не страшно, отправить дважды — заметно.
        if not await sync_to_async(_claim)(user, step):
            continue

        if await _send(bot, user.pk, text, keyboards.onboarding_nudge()):
            sent += 1
        else:
            failed += 1
    return sent, failed
