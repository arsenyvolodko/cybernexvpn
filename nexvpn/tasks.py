"""Периодическая работа.

Две задачи. Первая про надёжность: если панель лежала в момент оплаты, подписка
остаётся в статусе FAILED, и без повторов человек заплатил, но доступа не получил.
Вторая — напоминания об окончании подписки.

Всё остальное считается лениво, по обращению.

Отложенное понижение тарифа планировщика не требует — см.
`nexvpn.subscription.service.ensure_current_plan`.
"""

import logging

from celery import shared_task

from nexvpn.subscription import panel_sync

logger = logging.getLogger(__name__)


@shared_task()
def send_subscription_reminders():
    """Напомнить о скором окончании подписки и сказать, когда она кончилась.

    Живёт в celery, а не в процессе бота: бот может перезапускаться при деплое,
    а напоминания пропускать нельзя. Бот здесь поднимается разово, только чтобы
    отправить сообщения.
    """
    import asyncio

    from bot.main import build_bot
    from bot.notifications import send_due_expiry_notices, send_due_reminders
    from nexvpn.models import GlobalSettings

    if not GlobalSettings.load().reminders_enabled:
        # Выключено в админке: обычно потому, что люди ещё не знают о
        # перезапуске. Остальные задачи по расписанию при этом работают.
        logger.info("Напоминания выключены в настройках — пропускаю")
        return {"skipped": True}

    async def _run():
        bot = build_bot()
        try:
            # Один поднятый бот на оба дела: экземпляр создаётся ради отправки
            # и закрывается сразу, поднимать его дважды подряд незачем.
            reminders = await send_due_reminders(bot)
            expiry = await send_due_expiry_notices(bot)
            return reminders, expiry
        finally:
            await bot.session.close()

    (sent, failed), (ended_sent, ended_failed) = asyncio.run(_run())
    if sent or failed:
        logger.info("Напоминания: отправлено %s, не доставлено %s", sent, failed)
    if ended_sent or ended_failed:
        logger.info(
            "Сообщения об окончании: отправлено %s, не доставлено %s", ended_sent, ended_failed
        )
    return {"sent": sent, "failed": failed, "ended": ended_sent, "ended_failed": ended_failed}


@shared_task()
def sync_panel():
    """Догнать подписки, которые не доехали до Remnawave.

    При DEBUG=True не делаем ничего. Причина не теоретическая: локальный `.env`
    смотрит на боевую панель, и запущенный на машине разработчика celery-beat
    начинает раз в пять минут переписывать боевым пользователям сроки
    значениями из дев-базы. Такую же защиту носит команда `sync_panel`, но
    задача про неё не знала — а именно задача крутится по расписанию.
    """
    from django.conf import settings

    if settings.DEBUG:
        logger.warning(
            "DEBUG=True — пропускаю синхронизацию: панель в .env боевая (%s)",
            settings.PANEL_API_URL,
        )
        return {"skipped": True}

    ok, failed = panel_sync.sync_pending()
    if ok or failed:
        logger.info("Синхронизация с панелью: успешно %s, с ошибкой %s", ok, failed)
    return {"ok": ok, "failed": failed}


@shared_task()
def send_onboarding_nudges():
    """Подтолкнуть новичка, который открыл бота и не добавил ни одного устройства.

    Отдельно от `send_subscription_reminders`, хотя обе шлют сообщения: у той
    шаг в 10 минут, а здесь первое подталкивание само приходится на 10-ю
    минуту — на общей сетке оно опаздывало бы вдвое.

    При DEBUG=True не делаем ничего: локальный `.env` смотрит на боевую панель
    и держит боевой токен бота, так что запущенная на машине разработчика
    задача написала бы живым людям.
    """
    import asyncio

    from django.conf import settings

    if settings.DEBUG:
        logger.warning("DEBUG=True — пропускаю подталкивания: токен бота боевой")
        return {"skipped": True}

    from bot.main import build_bot
    from bot.onboarding import send_due_nudges

    async def _run():
        bot = build_bot()
        try:
            return await send_due_nudges(bot)
        finally:
            await bot.session.close()

    sent, failed = asyncio.run(_run())
    if sent or failed:
        logger.info("Подталкивания новичкам: отправлено %s, не доставлено %s", sent, failed)
    return {"sent": sent, "failed": failed}


@shared_task()
def enforce_device_limits():
    """Не дать накопиться устройствам сверх лимита тарифа.

    Реагирует опросом, а не вебхуком: `user_hwid_devices.added` заявлено в API
    панели, но эмпирически (10.09.2026) ни разу не пришло за 72+ часов живых
    добавлений. Опрос — единственный сигнал, который действительно работает.

    При DEBUG=True не делаем ничего — та же причина, что у `sync_panel`:
    локальный `.env` смотрит на боевую панель, и удалять боевые устройства
    с машины разработчика нельзя ни при каких условиях.
    """
    from django.conf import settings

    if settings.DEBUG:
        logger.warning(
            "DEBUG=True — пропускаю проверку лимита устройств: панель боевая (%s)",
            settings.PANEL_API_URL,
        )
        return {"skipped": True}

    from nexvpn.models import Subscription

    checked = removed_total = notified = failed = 0
    for subscription in Subscription.objects.exclude(panel_user_id=None).select_related("user", "plan"):
        checked += 1
        try:
            removed = panel_sync.reject_devices_added_over_limit(subscription)
        except Exception:
            failed += 1
            logger.warning(
                "Не смог проверить лимит устройств для %s", subscription.user_id, exc_info=True
            )
            continue
        if not removed:
            continue

        removed_total += len(removed)
        try:
            devices = panel_sync.list_devices(subscription)
        except Exception:
            devices = []

        from bot.notify import notify_device_limit_reached
        from bot.services import _device_title

        if notify_device_limit_reached(
            chat_id=subscription.user_id, devices=[_device_title(d) for d in devices]
        ):
            notified += 1

    if removed_total:
        logger.info(
            "Лимит устройств: проверено %s, снято %s, оповещено %s, ошибок %s",
            checked, removed_total, notified, failed,
        )
    return {"checked": checked, "removed": removed_total, "notified": notified, "failed": failed}


@shared_task()
def take_usage_snapshot():
    """Срез использования: кто онлайн, на какой ноде, сколько прокачал.

    Панель историю не хранит, поэтому копим её сами. Задача идемпотентна:
    пропущенный прогон не теряет трафик, он просто приедет следующим приростом.
    """
    from nexvpn import telemetry

    result = telemetry.take_snapshot()
    return {
        "seen": result.seen,
        "online": result.online,
        "traffic_delta": result.traffic_delta,
        "unknown_users": result.unknown_users,
    }


@shared_task()
def send_broadcast(broadcast_id: int):
    """Разослать сообщение из админки.

    В celery, а не в запросе админки: триста сообщений идут около минуты, и
    обрыв соединения не должен останавливать рассылку на полпути.
    """
    import asyncio

    from bot import broadcast
    from bot.main import build_bot

    async def _run():
        bot = build_bot()
        try:
            return await broadcast.run(bot, broadcast_id)
        finally:
            await bot.session.close()

    return asyncio.run(_run())


@shared_task()
def reconcile_payments():
    """Спросить ЮKassa про платежи, по которым мы ещё ничего не выдали.

    Вебхук может не дойти — у нас так и вышло, хостера приложения ЮKassa не
    пускает. Оплата без начисления это худшее, что может случиться в платном
    сервисе, поэтому не ждём их звонка, а звоним сами.
    """
    from nexvpn.subscription import reconcile

    result = reconcile.reconcile()
    return {
        "checked": result.checked,
        "applied": result.applied,
        "still_pending": result.still_pending,
        "failed": result.failed,
    }
