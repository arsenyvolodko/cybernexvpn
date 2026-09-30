"""Приём проб адресов выдачи подписки со страницы-мостика.

Особенность: шлёт это браузер человека, а не наш сервер. Поэтому ни токена,
ни подписи здесь нет — они всё равно оказались бы в исходнике страницы.
Защита другая: принимаем только известные нам адреса, только маленькое тело
и только через `sendBeacon`, который не умеет ждать ответа. Испортить такими
данными можно разве что собственную статистику.
"""

import logging

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import (
    api_view,
    parser_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from nexvpn.models import SubHostProbe, Subscription

logger = logging.getLogger(__name__)

MAX_ROWS = 8
# Медленнее этого считаем, что адрес душат: проверочный кусок сравним по
# размеру с конфигом, и если он едет дольше, конфиг не доедет тем более.
SLOW_MS = 3000


class BeaconParser(JSONParser):
    """Тело от `sendBeacon` помечено `text/plain`, и по-другому он не умеет.

    Указать заголовок ему нельзя, а обёртка в Blob с `application/json` уже
    не «простой» запрос: браузер сначала спросит разрешение предварительным
    запросом, которого на чужом домене никто не ждёт. Так что принимаем json,
    как бы он ни был подписан.
    """

    media_type = "text/plain"


class ProbeThrottle(AnonRateThrottle):
    """Потолок на адрес: мостик открывают единицы раз, а точка публичная.

    Ставится не от воровства — брать здесь нечего, — а чтобы никто не залил
    нам миллион строк и не испортил статистику, из-за которой всё и затевалось.
    """

    scope = "sub_probe"
    rate = "60/hour"


def _allowed_hosts():
    return {h.strip() for h in settings.SUB_PROBE_HOSTS if h.strip()}


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([ProbeThrottle])
@parser_classes([JSONParser, BeaconParser])
def ingest_sub_probe(request: Request) -> Response:
    """Результаты проб с устройства человека.

    Тело (может прийти как text/plain — так шлёт sendBeacon):
        {"short_uuid": "ShFz…", "platform": "ios",
         "results": [{"host": "sub-nex.com", "ok": true, "ms": 412},
                     {"host": "sub.cybernexapp.com", "ok": false, "ms": 0}]}
    """
    payload = request.data
    if not isinstance(payload, dict):
        return Response({"detail": "ожидал объект"}, status=400)

    results = payload.get("results")
    if not isinstance(results, list):
        return Response({"detail": "нужен results"}, status=400)

    short_uuid = str(payload.get("short_uuid") or "")[:63]
    platform = str(payload.get("platform") or "")[:31]
    user_id = None
    if short_uuid:
        user_id = (
            Subscription.objects.filter(panel_short_uuid=short_uuid)
            .values_list("user_id", flat=True)
            .first()
        )

    allowed = _allowed_hosts()
    rows = []
    for row in results[:MAX_ROWS]:
        if not isinstance(row, dict):
            continue
        host = str(row.get("host") or "")[:127]
        if host not in allowed:
            continue
        try:
            ms = max(0, min(120000, int(row.get("ms") or 0)))
        except (TypeError, ValueError):
            ms = 0
        if not row.get("ok"):
            outcome = SubHostProbe.Outcome.FAILED
        elif ms >= SLOW_MS:
            outcome = SubHostProbe.Outcome.SLOW
        else:
            outcome = SubHostProbe.Outcome.OK
        rows.append(
            SubHostProbe(
                host=host, outcome=outcome, ms=ms,
                user_id=user_id, short_uuid=short_uuid, platform=platform,
            )
        )

    SubHostProbe.objects.bulk_create(rows)
    return Response({"stored": len(rows)})
