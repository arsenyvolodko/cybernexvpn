"""Отправка сообщений в Telegram из синхронного кода (веб-процесс, celery).

Бот и Django — разные процессы, поэтому вебхук не может «попросить» бота
что-то отправить. Но токен один и тот же, а Bot API — обычный HTTP, так что
веб-процесс обращается к Telegram напрямую.

Здесь намеренно httpx, а не aiogram: поднимать асинхронный клиент и event loop
ради одного запроса из синхронной вьюхи — лишняя сложность и лишние способы
сломаться.
"""

import logging

import httpx
from django.conf import settings

from bot import texts
from bot.keyboards import keyboards

logger = logging.getLogger(__name__)

TIMEOUT = 10


def _api(method: str, payload: dict) -> bool:
    if not settings.TG_BOT_TOKEN:
        logger.error("TG_BOT_TOKEN не задан — отправить сообщение нельзя")
        return False
    url = f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/{method}"
    try:
        response = httpx.post(url, json=payload, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("Telegram недоступен (%s): %s", method, exc)
        return False
    if response.status_code >= 400:
        logger.warning("Telegram отказал (%s): %s", method, response.text[:300])
        return False
    return True


def notify_device_connected(chat_id: int, message_id: int, device_title: str) -> None:
    """Переписать экран «Добавить подписку» на «Готово»."""
    text = texts.CONNECT_SUCCESS.format(device=device_title)
    markup = keyboards.connected().model_dump(exclude_none=True)

    edited = _api(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": markup,
        },
    )
    if edited:
        return

    # Сообщение могло быть удалено или устареть — тогда просто пишем новое,
    # чтобы человек всё равно узнал, что подключение прошло.
    _api(
        "sendMessage",
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": markup},
    )
