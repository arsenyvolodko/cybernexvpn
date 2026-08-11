"""Проверка, что вебхук действительно пришёл от YooKassa.

Раньше их IP лежали в `ALLOWED_HOSTS`, где не делали ничего: Django сверяет там
заголовок `Host`, а не адрес клиента. Вебхук приходит на наш домен, так что
проверка не срабатывала никогда — и половина адресов была записана без масок.

Здесь проверка настоящая. Два места, где легко ошибиться, и оба важные:

1. **Какой элемент `X-Forwarded-For` брать.** Nginx дописывает реальный адрес
   в конец, поэтому доверять можно только хвосту. Первый элемент подставляет
   сам клиент — если сверять его, любой сможет прикинуться YooKassa, просто
   отправив нужный заголовок.
2. **Что делать при несовпадении.** По умолчанию только пишем в лог: если
   ошибиться с числом прокси, строгий режим тихо убьёт приём всех платежей.
   Включать `YOOKASSA_IP_CHECK_ENFORCE` стоит, посмотрев на реальные логи.
"""

import logging
from ipaddress import AddressValueError, ip_address, ip_network

from django.conf import settings

logger = logging.getLogger(__name__)


def _networks():
    for raw in settings.YOOKASSA_WEBHOOK_IPS:
        try:
            yield ip_network(raw.strip(), strict=False)
        except ValueError:
            logger.error("Некорректная сеть в YOOKASSA_WEBHOOK_IPS: %r", raw)


def get_client_ip(request):
    """Адрес источника запроса с поправкой на доверенные прокси."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]

    candidate = None
    if parts:
        # Считаем справа: столько записей, сколько наших прокси в цепочке.
        depth = max(1, settings.TRUSTED_PROXY_COUNT)
        candidate = parts[-depth] if len(parts) >= depth else parts[0]
    candidate = candidate or request.META.get("REMOTE_ADDR")

    if not candidate:
        return None
    try:
        return ip_address(candidate)
    except (ValueError, AddressValueError):
        logger.warning("Не удалось разобрать адрес клиента: %r", candidate)
        return None


def is_yookassa_ip(ip) -> bool:
    if ip is None:
        return False
    return any(ip in network for network in _networks())


def verify_webhook_source(request) -> tuple[bool, str]:
    """(пропускать ли запрос, описание) — описание для лога."""
    ip = get_client_ip(request)
    if is_yookassa_ip(ip):
        return True, str(ip)

    if settings.YOOKASSA_IP_CHECK_ENFORCE:
        logger.warning("Вебхук с постороннего адреса отклонён: %s", ip)
        return False, str(ip)

    logger.warning(
        "Вебхук с адреса %s не входит в диапазоны YooKassa. "
        "Пропускаю: строгая проверка выключена (YOOKASSA_IP_CHECK_ENFORCE).", ip,
    )
    return True, str(ip)
