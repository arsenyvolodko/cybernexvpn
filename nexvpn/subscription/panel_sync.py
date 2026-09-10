"""Синхронизация подписок с Remnawave.

Не очередь событий, а **идемпотентный reconcile**: Django считает желаемое
состояние (`expireAt`, `hwidDeviceLimit`, `status`) и приводит панель к нему.
Любой вызов можно безопасно повторить, а падение панели не теряет событие —
подписка остаётся в статусе PENDING и её добьёт периодическая задача.
"""

import logging

from django.utils.timezone import now

from nexvpn.enums import PanelSyncStatusEnum
from nexvpn.models import Subscription
from nexvpn.remnawave import RemnawaveClient, RemnawaveError

logger = logging.getLogger(__name__)


def sync_subscription(subscription: Subscription, client: RemnawaveClient | None = None) -> bool:
    """Привести пользователя в панели к состоянию подписки. True — получилось."""
    client = client or RemnawaveClient()
    user = subscription.user
    username = user.panel_username

    try:
        panel_user = client.get_user(username)
        if panel_user is None:
            panel_user = client.create_user(
                username=username,
                expire_at=subscription.expires_at,
                hwid_device_limit=subscription.device_limit,
                telegram_id=user.pk,
                email=user.email or None,
                squad_uuids=client.default_squad_uuids(),
            )
        else:
            panel_user = client.update_user(
                username=username,
                expire_at=subscription.expires_at,
                hwid_device_limit=subscription.device_limit,
                status="ACTIVE",
            )
    except RemnawaveError as exc:
        logger.warning("Не удалось синхронизировать подписку %s: %s", subscription.pk, exc)
        subscription.panel_status = PanelSyncStatusEnum.FAILED
        subscription.panel_error = str(exc)[:2000]
        subscription.save(update_fields=["panel_status", "panel_error", "updated_at"])
        return False

    subscription.panel_user_id = panel_user.get("id")
    subscription.panel_short_uuid = panel_user.get("shortUuid")
    subscription.subscription_url = panel_user.get("subscriptionUrl")
    subscription.panel_status = PanelSyncStatusEnum.SYNCED
    subscription.panel_synced_at = now()
    subscription.panel_error = ""
    subscription.save(
        update_fields=[
            "panel_user_id", "panel_short_uuid", "subscription_url",
            "panel_status", "panel_synced_at", "panel_error", "updated_at",
        ]
    )
    return True


def sync_pending(limit: int = 500) -> tuple[int, int]:
    """Догнать всё, что не доехало до панели. Возвращает (успешно, с ошибкой)."""
    client = RemnawaveClient()
    pending = (
        Subscription.objects
        .exclude(panel_status=PanelSyncStatusEnum.SYNCED)
        .select_related("user", "plan")[:limit]
    )
    ok = failed = 0
    for subscription in pending:
        if sync_subscription(subscription, client=client):
            ok += 1
        else:
            failed += 1
    return ok, failed


def list_devices(subscription: Subscription, client: RemnawaveClient | None = None) -> list[dict]:
    if subscription.panel_user_id is None:
        return []
    client = client or RemnawaveClient()
    return client.get_devices(subscription.panel_user_id)


def remove_device(subscription: Subscription, hwid: str, client: RemnawaveClient | None = None) -> None:
    if subscription.panel_user_id is None:
        raise RemnawaveError("Подписка ещё не заведена в панели")
    client = client or RemnawaveClient()
    client.delete_device(subscription.panel_user_id, hwid)


def reject_devices_added_over_limit(
    subscription: Subscription, client: RemnawaveClient | None = None
) -> list[dict] | None:
    """Снять устройства, добавленные сверх лимита с прошлой проверки.

    Существует потому, что панель не шлёт вебхук на добавление устройства:
    `user_hwid_devices.added` заявлено в её API, но эмпирически (10.09.2026,
    72+ часов живого трафика с реальными добавлениями) ни разу не пришло —
    только `.deleted` и `user.modified`. Опрос — единственный рабочий сигнал.

    Трогает только то, что появилось **после** прошлой проверки. Устройства,
    которые уже были сверх лимита до неё, не удаляются автоматически — с
    такими людьми разговор отдельный, а не тихое стирание. Возвращает `None`,
    если это первое наблюдение подписки (тогда только запоминает набор) или
    ничего снимать не пришлось; список снятых устройств иначе.
    """
    client = client or RemnawaveClient()
    devices = list_devices(subscription, client=client)
    current = {d["hwid"]: d for d in devices if d.get("hwid")}

    if subscription.known_device_hwids is None:
        subscription.known_device_hwids = list(current)
        subscription.save(update_fields=["known_device_hwids"])
        return None

    known = set(subscription.known_device_hwids)
    limit = subscription.device_limit
    if len(current) <= limit:
        if set(current) != known:
            subscription.known_device_hwids = list(current)
            subscription.save(update_fields=["known_device_hwids"])
        return None

    new_hwids = set(current) - known
    if not new_hwids:
        # Сверх лимита, но давно, ничего нового не появилось — не наше дело.
        return None

    # Из новых снимаем самые свежие по регистрации — ровно столько, сколько
    # нужно, чтобы влезть в лимит. Порядок внутри новых значения не имеет
    # (все они одинаково «нарушители»), но по времени регистрации — самый
    # понятный человеку критерий, если он вообще станет разбираться, что снято.
    excess = len(current) - limit
    offenders = sorted(
        (current[hwid] for hwid in new_hwids),
        key=lambda d: d.get("createdAt") or "",
        reverse=True,
    )[:excess]

    for device in offenders:
        remove_device(subscription, device["hwid"], client=client)
        logger.info(
            "Устройство сверх лимита снято при опросе: user=%s hwid=%s",
            subscription.user_id, device["hwid"],
        )

    removed = {d["hwid"] for d in offenders}
    subscription.known_device_hwids = [hwid for hwid in current if hwid not in removed]
    subscription.save(update_fields=["known_device_hwids"])
    return offenders


def trim_devices_to_limit(subscription: Subscription, client: RemnawaveClient | None = None) -> list[dict]:
    """Снести устройства сверх лимита тарифа, оставив самые свежие по активности.

    Нужно после понижения тарифа: панель не выкидывает уже зарегистрированные
    HWID сама, она лишь перестаёт принимать новые. Возвращает удалённые.
    """
    client = client or RemnawaveClient()
    devices = list_devices(subscription, client=client)
    limit = subscription.device_limit
    if len(devices) <= limit:
        return []

    devices.sort(key=lambda d: d.get("updatedAt") or d.get("createdAt") or "", reverse=True)
    excess = devices[limit:]
    for device in excess:
        client.delete_device(subscription.panel_user_id, device["hwid"])
        logger.info(
            "Удалено устройство сверх лимита: user=%s hwid=%s model=%s",
            subscription.user_id, device["hwid"], device.get("deviceModel"),
        )
    return excess
