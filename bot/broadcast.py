"""Массовая рассылка из админки.

Главное требование — живучесть. Триста сообщений подряд означают, что кто-то
обязательно заблокировал бота, у кого-то удалён чат, а Telegram где-то посреди
пачки попросит притормозить. Ни одно из этих событий не должно останавливать
рассылку: результат по каждому получателю пишется в `BroadcastDelivery`, и
видно поимённо, кто получил, а кто нет и почему.

Повторный запуск той же рассылки дошлёт только тех, кому в прошлый раз не
дошло: доставленные отфильтровываются по той же таблице.
"""

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils.timezone import now

from nexvpn.enums import BroadcastAudienceEnum, BroadcastStatusEnum
from nexvpn.models import Broadcast, BroadcastDelivery, NexUser, PanelPresence

logger = logging.getLogger(__name__)

# 20 сообщений в секунду: у Telegram потолок около 30, но на массовых рассылках
# он начинает притормаживать заметно раньше.
DELAY_BETWEEN_MESSAGES = 0.05

CONNECT_CALLBACK = "bcast_connect"
MENU_CALLBACK = "bcast_menu"
CONNECT_BUTTON_TEXT = "Подключиться бесплатно ⚡"
MENU_BUTTON_TEXT = "В меню"


def recipients(broadcast: Broadcast) -> list[NexUser]:
    """Кому уходит. Проверочная отправка бьёт только по администратору."""
    if broadcast.test_only:
        admin_id = settings.TG_ADMIN_USER_ID
        if not admin_id:
            return []
        return list(NexUser.objects.filter(pk=admin_id))

    users = NexUser.objects.all()
    if broadcast.audience == BroadcastAudienceEnum.LEGACY_GIFTED:
        # Ушедшие: на дату среза у них не было ни одного активного устройства,
        # и дни они получили подарком, а не переносом.
        users = users.filter(legacy_record__device_count=0)
    elif broadcast.audience == BroadcastAudienceEnum.LEGACY_CONVERTED:
        users = users.filter(legacy_record__device_count__gt=0)
    elif broadcast.audience == BroadcastAudienceEnum.NEW:
        users = users.filter(is_legacy=False, legacy_record__isnull=True)
    elif broadcast.audience == BroadcastAudienceEnum.NOT_ACTIVATED:
        # Не открывал бота новой версии ни разу: `activated_at` ставится при
        # первом же заходе. Практически это те, кого мы перенесли, а они о
        # перезапуске так и не узнали.
        users = users.filter(activated_at=None)
    elif broadcast.audience == BroadcastAudienceEnum.NOT_CONNECTED:
        # Зашёл, подписку получил, но туннель не поднял ни разу.
        #
        # Судим по `PanelPresence`: строка появляется со снимком телеметрии, а
        # `first_connected_at` в неё приходит из панели. Требуем именно
        # существующую строку — не «нет строки», а «строка есть, и в ней
        # пусто». Иначе при невыполнявшейся телеметрии (celery-beat в проде
        # поднят не всегда) группа молча раздуется до всех сразу, и объявление
        # «у тебя не получилось подключиться» уедет тем, у кого всё работает.
        never_connected = PanelPresence.objects.filter(first_connected_at=None).values("user_id")
        users = users.exclude(activated_at=None).filter(pk__in=never_connected)

    already = BroadcastDelivery.objects.filter(
        broadcast=broadcast, is_delivered=True
    ).values_list("user_id", flat=True)
    return list(users.exclude(pk__in=already).order_by("pk"))


def keyboard_for(broadcast: Broadcast):
    """Кнопки объявления.

    Свои callback'и, а не менюшные: обычная кнопка меню правит сообщение на
    месте, а объявление затирать нельзя — человек может захотеть перечитать
    его позже. Поэтому у этих кнопок отдельные обработчики, которые лишь
    снимают клавиатуру и присылают нужный экран новым сообщением.
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    if broadcast.with_connect_button:
        rows.append([InlineKeyboardButton(
            text=CONNECT_BUTTON_TEXT, callback_data=CONNECT_CALLBACK, style="success"
        )])
    if broadcast.with_menu_button:
        rows.append([InlineKeyboardButton(text=MENU_BUTTON_TEXT, callback_data=MENU_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def _send_one(bot, chat_id: int, text: str, keyboard) -> tuple[bool, str]:
    try:
        await bot.send_message(chat_id, text, reply_markup=keyboard)
        return True, ""
    except TelegramRetryAfter as exc:
        # Просят подождать — ждём и пробуем ещё раз, это штатная ситуация.
        await asyncio.sleep(exc.retry_after + 1)
        try:
            await bot.send_message(chat_id, text, reply_markup=keyboard)
            return True, ""
        except TelegramAPIError as retry_exc:
            return False, str(retry_exc)[:250]
    except TelegramForbiddenError:
        return False, "бот заблокирован"
    except TelegramAPIError as exc:
        return False, str(exc)[:250]
    except Exception as exc:  # сеть, таймаут — не повод ронять всю рассылку
        logger.warning("Не удалось отправить %s: %s", chat_id, exc)
        return False, str(exc)[:250]


async def run(bot, broadcast_id: int) -> dict:
    broadcast = await sync_to_async(Broadcast.objects.get)(pk=broadcast_id)
    targets = await sync_to_async(recipients)(broadcast)

    await sync_to_async(Broadcast.objects.filter(pk=broadcast_id).update)(
        status=BroadcastStatusEnum.SENDING, started_at=now()
    )
    keyboard = keyboard_for(broadcast)
    sent = failed = 0

    for user in targets:
        delivered, error = await _send_one(bot, user.pk, broadcast.text, keyboard)
        await sync_to_async(BroadcastDelivery.objects.update_or_create)(
            broadcast=broadcast,
            user=user,
            defaults={"is_delivered": delivered, "error": error},
        )
        if delivered:
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(DELAY_BETWEEN_MESSAGES)

    # Статус «сорвалась» — только когда не дошло вообще никому: одна-две
    # блокировки при трёх сотнях получателей это норма, а не провал.
    status = BroadcastStatusEnum.SENT if sent or not targets else BroadcastStatusEnum.FAILED
    await sync_to_async(Broadcast.objects.filter(pk=broadcast_id).update)(
        status=status,
        finished_at=now(),
        sent_count=broadcast.sent_count + sent,
        failed_count=failed,
    )
    logger.info("Рассылка «%s»: отправлено %s, не дошло %s", broadcast.title, sent, failed)
    return {"sent": sent, "failed": failed}
