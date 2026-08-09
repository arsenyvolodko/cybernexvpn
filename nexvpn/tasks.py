"""Периодическая работа.

Задача ровно одна и она про надёжность: если панель лежала в момент оплаты,
подписка остаётся в статусе FAILED, и без повторов человек заплатил, но доступа
не получил. Всё остальное считается лениво, по обращению.

Отложенное понижение тарифа планировщика не требует — см.
`nexvpn.subscription.service.ensure_current_plan`.
"""

import logging

from celery import shared_task

from nexvpn.subscription import panel_sync

logger = logging.getLogger(__name__)


@shared_task()
def sync_panel():
    """Догнать подписки, которые не доехали до Remnawave."""
    ok, failed = panel_sync.sync_pending()
    if ok or failed:
        logger.info("Синхронизация с панелью: успешно %s, с ошибкой %s", ok, failed)
    return {"ok": ok, "failed": failed}
