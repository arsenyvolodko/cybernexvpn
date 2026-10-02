"""Сроки сертификатов на нодах.

Понадобилось после 30.09.2026: сертификат `*.pineferry.com` истекал 7 ноября
одновременно на всех нодах, продлевался только на одной, и узнать об этом было
негде — ни в панели, ни в дашборде. Туннель, который однажды просто перестанет
отвечать, обходится дороже любого экрана.

Проверяем не по файлам на дисках, а **рукопожатием снаружи** — тем же способом,
каким это увидит приложение человека. Файл на ноде может быть свежим, а xray
при этом держать в памяти прежний: ровно так и выглядит забытый перезапуск.

Сеть здесь медленная и ненадёжная, поэтому страница в неё не ходит: обходом
занимается задача по расписанию, результат лежит в кэше, а дашборд его только
читает. Нет в кэше — честно пишем «не проверяли», а не рисуем прочерк, который
легко принять за «сертификат не нужен».
"""

from __future__ import annotations

import datetime as dt
import logging
import socket
import ssl

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY = "node_certificates"
TTL = 7 * 24 * 3600          # Храним дольше, чем период обхода: лучше вчерашнее
                             # значение, чем пустая клетка при сбое задачи.
TIMEOUT = 5
# Красим, когда до конца меньше этого. Let's Encrypt продлевает за 30 дней, так
# что три недели — это уже «автопродление не сработало», а не «скоро истекает».
ALARM_DAYS = 21

# Где у нас вообще живёт TLS с нашим сертификатом. Список явный: угадывать по
# роли нельзя — у cs1 сертификата нет вовсе, он отдаёт только REALITY по адресу,
# а у ru2 одна машина с двумя именами на разных портах.
TLS_ENDPOINTS: dict[str, list[tuple[str, int]]] = {
    "de1-ovh": [("de1.pineferry.com", 7443)],
    "pl1-ovh": [("pl1.pineferry.com", 7443)],
    "de2-ghostnet": [("de2.pineferry.com", 7443)],
    "eu1-ovh": [("eu1.pineferry.com", 7443)],
    "ru2-eurobyte": [("ru2.pineferry.com", 7443), ("ru2b.pineferry.com", 8080)],
}


def _context() -> ssl.SSLContext:
    """Нам нужно прочитать сертификат, а не поверить ему.

    Проверку имени и доверия снимаем намеренно: просроченный или чужой
    сертификат — это и есть то, что мы ищем, а с проверкой рукопожатие
    оборвалось бы раньше, чем мы его увидим.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def probe(host: str, port: int) -> dict | None:
    """Чем отвечает вход: имя в сертификате и до какого числа он годен."""
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as raw:
            with _context().wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
                info = tls.getpeercert()
    except (OSError, ssl.SSLError) as exc:
        logger.info("Сертификат %s:%s не прочитали: %s", host, port, exc)
        return None
    if not der:
        return None

    until = None
    raw_until = (info or {}).get("notAfter")
    if raw_until:
        try:
            until = dt.datetime.strptime(raw_until, "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=dt.timezone.utc
            )
        except ValueError:
            until = None
    subject = ""
    for part in (info or {}).get("subject", ()):
        for key, value in part:
            if key == "commonName":
                subject = value
    return {"host": host, "port": port, "name": subject,
            "until": until.date().isoformat() if until else None}


def refresh() -> dict:
    """Обойти все входы и запомнить результат. Зовётся задачей по расписанию."""
    today = timezone.localdate()
    out: dict[str, dict] = {}
    for node, endpoints in TLS_ENDPOINTS.items():
        rows = []
        for host, port in endpoints:
            found = probe(host, port)
            if found:
                rows.append(found)
        if not rows:
            out[node] = {"checked": True, "reachable": False}
            logger.warning("Сертификат ноды %s не прочитался ни по одному входу", node)
            continue
        # Если имён несколько, тревожит самое раннее: туннель ломается по
        # первому истёкшему, а не по среднему.
        rows.sort(key=lambda r: r["until"] or "9999-12-31")
        first = rows[0]
        days = None
        if first["until"]:
            days = (dt.date.fromisoformat(first["until"]) - today).days
            if days <= ALARM_DAYS:
                logger.warning(
                    "Сертификат %s (%s) истекает через %s дн. — автопродление не сработало",
                    node, first["host"], days,
                )
        out[node] = {"checked": True, "reachable": True, "until": first["until"],
                     "days": days, "name": first["name"], "host": first["host"],
                     "endpoints": rows}
    cache.set(CACHE_KEY, out, TTL)
    return out


def known() -> dict:
    """Что лежит в кэше. Пусто — значит задача ещё не отработала."""
    return cache.get(CACHE_KEY) or {}
