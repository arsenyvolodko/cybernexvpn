"""Приём статистики по туннелям с нод.

Поток встречный к остальной телеметрии: не мы ходим в панель, а нода стучится
к нам. Иначе никак — access log Xray живёт внутри контейнера на ноде, наружу
его никто не отдаёт, а ходить с бэкенда по ssh на три сервера ради статистики
означало бы держать в контейнере приватный ключ от продовых нод.

Авторизация — общий секрет в заголовке. Полноценная подпись тела здесь ничего
не добавляет: канал уже https, а секрет одноразово кладётся на ноду руками.

Значения приходят абсолютные (итог за сутки), поэтому эндпоинт идемпотентен:
повторная доставка перезапишет строку тем же числом, а не удвоит его.
"""

import hmac
import logging

from django.conf import settings
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from nexvpn import telemetry

logger = logging.getLogger(__name__)

TOKEN_HEADER = "HTTP_X_NODE_TOKEN"
MAX_ROWS = 5000


def _authorized(request: Request) -> bool:
    expected = settings.NODE_TELEMETRY_TOKEN
    if not expected:
        logger.error("NODE_TELEMETRY_TOKEN не задан — статистику с нод принимать нельзя")
        return False
    return hmac.compare_digest(expected, request.META.get(TOKEN_HEADER, ""))


@api_view(["POST"])
def ingest_inbound_usage(request: Request) -> Response:
    """Счётчики соединений по инбаундам за сутки.

    Тело:
        {"node": "eu1-ovh", "rows": [
            {"panel_user_id": 413, "inbound_tag": "Hysteria2-Obfs",
             "via_relay": false, "date": "2026-08-14",
             "connections": 128, "last_seen": "2026-08-14T16:13:00Z"}
        ]}
    """
    if not _authorized(request):
        return Response(status=403)

    payload = request.data if isinstance(request.data, dict) else {}
    node_name = (payload.get("node") or "").strip()
    rows = payload.get("rows")
    if not node_name or not isinstance(rows, list):
        return Response({"detail": "нужны node и rows"}, status=400)
    if len(rows) > MAX_ROWS:
        # Столько строк за сутки быть не может: либо сборщик сошёл с ума, либо
        # это не наш сборщик. Молча обрезать нельзя — получим тихо неполные данные.
        return Response({"detail": f"слишком много строк: {len(rows)}"}, status=400)

    try:
        result = telemetry.record_inbound_usage(node_name, rows)
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Кривая статистика с ноды %s: %s", node_name, exc)
        return Response({"detail": "не разобрал строки"}, status=400)

    return Response({"stored": result.stored, "unknown_users": result.unknown_users})
