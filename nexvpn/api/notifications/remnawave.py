"""Приём вебхуков Remnawave.

Формат снят с живой панели 10.08.2026:

    POST, user-agent: Remnawave
    x-remnawave-signature: <hmac-sha256 hex от тела запроса>
    x-remnawave-timestamp: 2026-08-10T12:21:03.194Z
    {"scope": "user", "event": "user.modified", "timestamp": "...", "data": {...}}

Подпись считается по **сырому телу** запроса, а не по пересобранному JSON:
пересборка в Python может переставить пробелы или порядок ключей, и проверка
развалится на ровном месте.

`timestamp` лежит внутри подписанного тела, поэтому подделать его нельзя —
и на нём же строится защита от повторов: старые доставки отбрасываем.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone

from django.conf import settings
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from nexvpn.enums import PanelSyncStatusEnum, SubscriptionEventReasonEnum
from nexvpn.models import DeviceConnectionWatch, SentReminder, Subscription, SubscriptionEvent

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "HTTP_X_REMNAWAVE_SIGNATURE"
MAX_AGE = timedelta(minutes=5)

EVENT_HWID_ADDED = "user_hwid_devices.added"
EVENT_USER_MODIFIED = "user.modified"

# Меньше минуты разницы — это наше же изменение, вернувшееся эхом, или
# округление при передаче. Реагировать на такое нельзя: мы пишем в панель,
# панель шлёт нам событие, мы пишем себе — и так по кругу.
ECHO_TOLERANCE = timedelta(minutes=1)


def _signature_matches(raw_body: bytes, provided: str) -> bool:
    secret = settings.REMNAWAVE_WEBHOOK_SECRET
    if not secret:
        logger.error("REMNAWAVE_WEBHOOK_SECRET не задан — вебхуки принимать нельзя")
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided or "")


def _is_fresh(payload: dict) -> bool:
    raw = payload.get("timestamp")
    if not raw:
        return True
    try:
        sent_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    return abs(datetime.now(timezone.utc) - sent_at) <= MAX_AGE


@api_view(["POST"])
def handle_webhook(request: Request) -> Response:
    if not _signature_matches(request.body, request.META.get(SIGNATURE_HEADER, "")):
        logger.warning("Вебхук Remnawave с неверной подписью отклонён")
        return Response(status=403)

    try:
        payload = json.loads(request.body)
    except ValueError:
        return Response(status=400)

    if not _is_fresh(payload):
        # Повтор старой доставки: подпись верная, но событие давно неактуально.
        logger.warning("Пропускаю устаревший вебхук: %s", payload.get("event"))
        return Response(status=200)

    event = payload.get("event")
    data = payload.get("data") or {}

    if event == EVENT_HWID_ADDED:
        _handle_device_added(data)
    elif event == EVENT_USER_MODIFIED:
        _handle_user_modified(data)
    else:
        logger.info("Вебхук Remnawave: %s", event)

    return Response(status=200)


def _handle_device_added(data: dict) -> None:
    """Появилось новое устройство — если человек его сейчас ждёт, сказать ему."""
    user_id = data.get("userId") or (data.get("user") or {}).get("telegramId")
    hwid = data.get("hwid")
    if user_id is None:
        logger.info("Событие об устройстве без userId: %s", data)
        return

    watch = DeviceConnectionWatch.objects.filter(user__subscription__panel_user_id=user_id).first()
    if watch is None:
        logger.info("Устройство %s добавлено, но никто его не ждёт", hwid)
        return
    if hwid and hwid in (watch.known_hwids or []):
        # Такое устройство уже было до начала ожидания — не наш случай.
        return

    # Замок: строку удаляет тот, кто первым добрался. Второй ничего не отправит.
    claimed, _ = DeviceConnectionWatch.objects.filter(pk=watch.pk).delete()
    if not claimed:
        return

    from bot.notify import notify_device_connected

    notify_device_connected(
        chat_id=watch.chat_id,
        message_id=watch.message_id,
        device_title=data.get("deviceModel") or data.get("platform") or "Устройство",
    )


def _parse_expire(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Не разобрал expireAt из вебхука: %r", raw)
        return None


def _handle_user_modified(data: dict) -> None:
    """Срок правили прямо в панели — принять это у себя.

    Источник правды всё же наш: в `SubscriptionEvent` лежит, **почему** у
    человека такой срок, и панель этой истории не знает. Поэтому изменение не
    просто записывается в поле, а фиксируется событием — иначе через полгода
    дата окажется необъяснимой, и никто не поймёт, откуда она взялась.

    Принимаем только у подписок в состоянии `synced`. `pending` означает, что
    мы сами сейчас что-то меняем и наша правка всё равно перезапишет панель;
    подхватить в этот момент её значение — значит потерять своё.
    """
    panel_user_id = data.get("id") or data.get("userId")
    expires_at = _parse_expire(data.get("expireAt"))
    if panel_user_id is None or expires_at is None:
        return

    subscription = (
        Subscription.objects.select_related("user", "plan")
        .filter(panel_user_id=panel_user_id)
        .first()
    )
    if subscription is None:
        telegram_id = data.get("telegramId")
        if telegram_id:
            subscription = (
                Subscription.objects.select_related("user", "plan")
                .filter(user_id=telegram_id)
                .first()
            )
    if subscription is None:
        logger.info("Панель изменила пользователя %s, которого у нас нет", panel_user_id)
        return

    before = subscription.expires_at
    if abs(expires_at - before) < ECHO_TOLERANCE:
        # Наше же изменение вернулось эхом — панель шлёт событие и на свои
        # правки, и на наши.
        return

    if subscription.panel_status != PanelSyncStatusEnum.SYNCED:
        logger.info(
            "Панель изменила срок у %s, но у нас правка в очереди (%s) — оставляем своё",
            subscription.user_id, subscription.panel_status,
        )
        return

    subscription.expires_at = expires_at
    subscription.save(update_fields=["expires_at", "updated_at"])

    # Период сдвинулся — напоминания по нему шлём заново, ровно как при
    # начислении дней. Иначе человек с продлённой в панели подпиской не
    # получит ни одного предупреждения о новом окончании.
    SentReminder.objects.filter(subscription=subscription).delete()

    SubscriptionEvent.objects.create(
        user=subscription.user,
        subscription=subscription,
        reason=SubscriptionEventReasonEnum.ADMIN_ADJUSTMENT,
        delta_days=round((expires_at - before).total_seconds() / 86400),
        plan=subscription.plan,
        price_month=subscription.plan.price_month,
        expires_at_before=before,
        expires_at_after=expires_at,
        comment="Изменено в панели",
    )
    logger.info(
        "Приняли из панели новый срок для %s: %s -> %s",
        subscription.user_id, before, expires_at,
    )
