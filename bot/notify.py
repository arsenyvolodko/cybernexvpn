"""Отправка сообщений в Telegram из синхронного кода (веб-процесс, celery).

Бот и Django — разные процессы, поэтому вебхук не может «попросить» бота
что-то отправить. Но токен один и тот же, а Bot API — обычный HTTP, так что
веб-процесс обращается к Telegram напрямую.

Здесь намеренно httpx, а не aiogram: поднимать асинхронный клиент и event loop
ради одного запроса из синхронной вьюхи — лишняя сложность и лишние способы
сломаться.
"""

import json
import logging

import httpx
from django.conf import settings

from bot import media, texts
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


def _api_photo(payload: dict, photo_path) -> bool:
    """Отправить фото файлом. Отдельно от `_api`, потому что это multipart.

    Файл заливается при каждой отправке — их единицы в день, и держать кэш
    `file_id` в двух процессах (бот и веб) ради этого дороже, чем перезалить.
    В multipart `reply_markup` передаётся строкой JSON, а не объектом.
    """
    if not settings.TG_BOT_TOKEN:
        logger.error("TG_BOT_TOKEN не задан — отправить фото нельзя")
        return False
    url = f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/sendPhoto"
    data = dict(payload)
    if isinstance(data.get("reply_markup"), dict):
        data["reply_markup"] = json.dumps(data["reply_markup"])
    try:
        with open(photo_path, "rb") as photo:
            response = httpx.post(url, data=data, files={"photo": photo}, timeout=TIMEOUT)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("Не отправилось фото: %s", exc)
        return False
    if response.status_code >= 400:
        logger.warning("Telegram отказал (sendPhoto): %s", response.text[:300])
        return False
    return True


def notify_device_connected(chat_id: int, message_id: int, device_title: str) -> None:
    """Сменить экран «Добавить подписку» на «Готово» — с картинкой.

    Текстовое сообщение нельзя превратить в сообщение с фото правкой, поэтому
    шлём новое и удаляем прежнее. Сначала отправка, потом удаление: сорвётся
    отправка — у человека останется старый экран, а не пустота.
    """
    text = texts.CONNECT_SUCCESS.format(device=device_title)
    markup = keyboards.connected().model_dump(exclude_none=True)

    if len(text) <= media.CAPTION_LIMIT and media.CONNECT_SUCCESS_PHOTO.exists():
        sent = _api_photo(
            {"chat_id": chat_id, "caption": text, "parse_mode": "HTML", "reply_markup": markup},
            media.CONNECT_SUCCESS_PHOTO,
        )
        if sent:
            _api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
            return

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


PAYMENT_OK_CALLBACK = "payment_ok"


def _payment_ok_markup() -> dict:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="Отлично 👍", callback_data=PAYMENT_OK_CALLBACK, style="success"
            )
        ]]
    )
    return keyboard.model_dump(exclude_none=True)


def notify_payment_applied(chat_id: int, text: str, screen_message_id: int | None = None) -> None:
    """Сказать человеку, что оплата прошла и дни начислены.

    Правим тот самый экран «Всё готово к оплате», если он ещё цел: человек
    оттуда и ушёл платить, туда же логично и вернуться. Не вышло — шлём новым
    сообщением с той же кнопкой, чтобы поведение не зависело от того, удалось
    ли попасть в старое сообщение.
    """
    payload = {"text": text, "parse_mode": "HTML", "reply_markup": _payment_ok_markup()}

    if screen_message_id and _api(
        "editMessageText", {"chat_id": chat_id, "message_id": screen_message_id, **payload}
    ):
        return

    _api("sendMessage", {"chat_id": chat_id, **payload})
