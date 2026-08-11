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
    """Напомнить об окончании подписки.

    Живёт в celery, а не в процессе бота: бот может перезапускаться при деплое,
    а напоминания пропускать нельзя. Бот здесь поднимается разово, только чтобы
    отправить сообщения.
    """
    import asyncio

    from bot.main import build_bot
    from bot.notifications import send_due_reminders

    async def _run():
        bot = build_bot()
        try:
            return await send_due_reminders(bot)
        finally:
            await bot.session.close()

    sent, failed = asyncio.run(_run())
    if sent or failed:
        logger.info("Напоминания: отправлено %s, не доставлено %s", sent, failed)
    return {"sent": sent, "failed": failed}


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
