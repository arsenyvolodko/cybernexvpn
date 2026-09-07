"""Дождаться, что устройство действительно подключилось, и переписать экран.

Когда человек нажимает «Добавить подписку», приложение забирает подписку по
ссылке и вместе с ней шлёт заголовок `x-hwid`. В этот момент в панели появляется
новое устройство — это и есть сигнал, что всё получилось. Раньше экран так и
висел на «Шаг 2 из 2», и человек не понимал, сработало или нет.

Сейчас мы опрашиваем панель, потому что Django ещё не выкачен и вебхуку от
панели просто некуда прийти. У Remnawave есть событие `user_hwid_devices.added` —
когда появится публичный адрес, поллинг заменится на него, а `announce_connected`
останется тем же.

Две вещи, без которых это опасно:

- **Метка экрана.** Задача правит сообщение спустя минуты. Если человек за это
  время ушёл в другой раздел, править нельзя — затрём то, что он сейчас видит.
- **Только новые HWID.** Сравниваем с набором, снятым до нажатия: у человека уже
  могли быть подключённые устройства, и они не должны считаться за успех.
"""

import asyncio
import logging

from aiogram.types import FSInputFile

from bot import media, texts
from bot.screen_state import is_current_screen
from bot.keyboards import keyboards
from bot.services import Device, claim_connection_watch, list_devices
from nexvpn.models import NexUser

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 3
POLL_TIMEOUT_SECONDS = 180


async def announce_connected(bot, chat_id: int, message_id: int, device: Device) -> None:
    """Сообщить, что устройство на связи. Точка входа и для поллинга, и для вебхука.

    Экран «Шаг 2 из 2» заменяется новым сообщением с картинкой, а не правится.
    Так приходится делать потому, что Bot API не умеет превращать текстовое
    сообщение в сообщение с фото: `editMessageMedia` работает только там, где
    медиа уже было. Порядок — сначала отправить, потом удалить старое: если
    отправка сорвётся, человек останется хотя бы со старым экраном.
    """
    caption = texts.CONNECT_SUCCESS.format(device=device.title)

    if len(caption) <= media.CAPTION_LIMIT and media.CONNECT_SUCCESS_PHOTO.exists():
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=FSInputFile(media.CONNECT_SUCCESS_PHOTO),
                caption=caption,
                reply_markup=keyboards.connected(),
            )
        except Exception:
            logger.warning("Не отправилось фото подключения — шлём текстом", exc_info=True)
        else:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                # Не удалилось — над картинкой останется висеть «Шаг 2 из 2».
                # Некрасиво, но не сломано, и это не повод ничего не показывать.
                logger.debug("Старый экран подключения не удалился", exc_info=True)
            return

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=caption,
            reply_markup=keyboards.connected(),
        )
    except Exception:
        logger.debug("Не удалось обновить экран подключения", exc_info=True)


async def watch_for_new_device(
    bot,
    chat_id: int,
    message_id: int,
    user: NexUser,
    known_hwids: set[str],
    screen: str,
) -> None:
    deadline = asyncio.get_running_loop().time() + POLL_TIMEOUT_SECONDS

    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

        # Человек ушёл с экрана — дальше следить не за чем.
        if not is_current_screen(chat_id, screen):
            return

        try:
            devices = await list_devices(user)
        except Exception:
            logger.debug("Панель недоступна во время ожидания устройства", exc_info=True)
            continue
        if devices is None:
            continue

        fresh = [device for device in devices if device.hwid not in known_hwids]
        if not fresh:
            continue

        logger.info("Устройство подключилось: user=%s %s", user.pk, fresh[0].title)
        # Замок общий с вебхуком: если он успел раньше, второго сообщения не будет.
        if await claim_connection_watch(user) and is_current_screen(chat_id, screen):
            await announce_connected(bot, chat_id, message_id, fresh[0])
        return


def start_watching(bot, chat_id, message_id, user, known_hwids, screen) -> None:
    """Запустить наблюдение в фоне, не задерживая ответ на нажатие."""
    task = asyncio.create_task(
        watch_for_new_device(bot, chat_id, message_id, user, known_hwids, screen)
    )
    # Без ссылки задачу может собрать сборщик мусора прямо во время ожидания.
    _running.add(task)
    task.add_done_callback(_running.discard)


_running: set[asyncio.Task] = set()
