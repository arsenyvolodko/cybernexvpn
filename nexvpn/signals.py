"""Сигналы: держим панель в согласии с нашей базой.

Удаляют людей руками — в админке, из шелла, при разборе дублей. Если после
этого пользователь остаётся в Remnawave, его подписка продолжает работать:
доступ есть, а в нашей базе о нём ни строчки, и ни продлить, ни отключить его
уже нечем.
"""

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from nexvpn.models import Subscription
from nexvpn.remnawave.client import RemnawaveClient, RemnawaveError, RemnawaveNotFound

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Subscription, dispatch_uid="delete_panel_user")
def delete_panel_user(sender, instance: Subscription, **kwargs):
    """Удалить пользователя в панели вслед за подпиской.

    Вешаем на Subscription, а не на NexUser: `panel_user_id` живёт здесь, и при
    удалении пользователя каскад всё равно снесёт подписку, а сигнал сработает.

    Делаем синхронно и сознательно глотаем ошибку. Через celery было бы
    аккуратнее, но тогда удаление из шелла на машине без воркера молча ничего
    не сделает. Если панель недоступна, останется висеть лишний пользователь —
    неприятно, но не ломает ничего; в логе об этом будет предупреждение.
    """
    if not instance.panel_user_id:
        return
    try:
        RemnawaveClient().delete_user(instance.panel_user_id)
    except RemnawaveNotFound:
        logger.info("В панели уже нет пользователя %s", instance.panel_user_id)
    except RemnawaveError:
        logger.warning(
            "Не удалось удалить пользователя %s из панели — останется лишним",
            instance.panel_user_id,
            exc_info=True,
        )
    else:
        logger.info("Пользователь %s удалён из панели вслед за подпиской", instance.panel_user_id)
