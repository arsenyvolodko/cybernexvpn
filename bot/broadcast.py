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
import re
from dataclasses import dataclass

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils.timezone import now

from nexvpn.enums import BroadcastAudienceEnum, BroadcastStatusEnum
from nexvpn.models import (
    Broadcast,
    BroadcastDelivery,
    BroadcastPollVote,
    NexUser,
    PanelPresence,
)

logger = logging.getLogger(__name__)

# 20 сообщений в секунду: у Telegram потолок около 30, но на массовых рассылках
# он начинает притормаживать заметно раньше.
DELAY_BETWEEN_MESSAGES = 0.05

CONNECT_CALLBACK = "bcast_connect"
MENU_CALLBACK = "bcast_menu"
# «В меню» у рассылки с кнопками рефералки. Отдельный callback, чтобы не менять
# поведение обычной: та снимает всю клавиатуру, эта — только саму себя.
MENU_KEEP_REFERRAL_CALLBACK = "bcast_menu_ref"
CONNECT_BUTTON_TEXT = "Подключиться бесплатно ⚡"
MENU_BUTTON_TEXT = "В меню"

# Анимированный эмодзи в тексте рассылки: `{emoji_<id>}` или `{emoji_<id>:💸}`.
# После двоеточия — обычный эмодзи, который Telegram покажет, если кастомный
# отрисовать не сможет; без него подставляем DEFAULT_EMOJI_FALLBACK.
EMOJI_PLACEHOLDER = re.compile(r"\{emoji_(\d+)(?::([^{}\s]+))?\}")
DEFAULT_EMOJI_FALLBACK = "⭐"


def render_text(text: str) -> str:
    """Развернуть плейсхолдеры эмодзи в теги Telegram. Остальной текст не трогаем."""
    return EMOJI_PLACEHOLDER.sub(
        lambda m: f'<tg-emoji emoji-id="{m.group(1)}">{m.group(2) or DEFAULT_EMOJI_FALLBACK}</tg-emoji>',
        text,
    )


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


def keyboard_for(broadcast: Broadcast, user_id: int | None = None):
    """Кнопки объявления.

    Свои callback'и, а не менюшные: обычная кнопка меню правит сообщение на
    месте, а объявление затирать нельзя — человек может захотеть перечитать
    его позже. Поэтому у этих кнопок отдельные обработчики, которые лишь
    снимают клавиатуру и присылают нужный экран новым сообщением.

    Кнопки рефералки несут ссылку конкретного человека, поэтому такая
    клавиатура собирается под каждого получателя (`user_id`).
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from bot.keyboards.keyboards import referral_buttons
    from bot.services import referral_link

    rows = []
    if broadcast.with_connect_button:
        rows.append([InlineKeyboardButton(
            text=CONNECT_BUTTON_TEXT, callback_data=CONNECT_CALLBACK, style="success"
        )])
    if broadcast.with_referral_buttons and user_id is not None:
        rows.extend([button] for button in referral_buttons(referral_link(user_id)))
    if broadcast.with_menu_button:
        menu_callback = (
            MENU_KEEP_REFERRAL_CALLBACK if broadcast.with_referral_buttons else MENU_CALLBACK
        )
        rows.append([InlineKeyboardButton(text=MENU_BUTTON_TEXT, callback_data=menu_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def _deliver(bot, chat_id: int, broadcast: Broadcast, keyboard):
    """Одна отправка. Способ зависит от того, откуда взялось сообщение.

    Составленное в боте копируем: `copy_message` переносит вложение, подпись и
    разметку как есть, без пометки «переслано». Написанное в админке — обычный
    текст.
    """
    if broadcast.is_poll:
        # Неанонимный: иначе Telegram не скажет, кто за что голосовал. В
        # вопросе из разметки работают только эмодзи — это проверяет clean().
        return await bot.send_poll(
            chat_id,
            question=render_text(broadcast.text),
            options=broadcast.options,
            is_anonymous=False,
            allows_multiple_answers=broadcast.poll_allows_multiple,
            question_parse_mode="HTML",
            reply_markup=keyboard,
        )
    if broadcast.is_copied:
        return await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=broadcast.source_chat_id,
            message_id=broadcast.source_message_id,
            reply_markup=keyboard,
        )
    return await bot.send_message(chat_id, render_text(broadcast.text), reply_markup=keyboard)


async def _send_one(bot, chat_id: int, broadcast: Broadcast, keyboard) -> tuple[bool, str, str]:
    """Отправить одному. Возвращает (дошло, ошибка, id опроса — если это опрос)."""
    try:
        message = await _deliver(bot, chat_id, broadcast, keyboard)
        return True, "", _poll_id(message)
    except TelegramRetryAfter as exc:
        # Просят подождать — ждём и пробуем ещё раз, это штатная ситуация.
        await asyncio.sleep(exc.retry_after + 1)
        try:
            message = await _deliver(bot, chat_id, broadcast, keyboard)
            return True, "", _poll_id(message)
        except TelegramAPIError as retry_exc:
            return False, str(retry_exc)[:250], ""
    except TelegramForbiddenError:
        return False, "бот заблокирован", ""
    except TelegramAPIError as exc:
        return False, str(exc)[:250], ""
    except Exception as exc:  # сеть, таймаут — не повод ронять всю рассылку
        logger.warning("Не удалось отправить %s: %s", chat_id, exc)
        return False, str(exc)[:250], ""


def _poll_id(message) -> str:
    poll = getattr(message, "poll", None)
    return getattr(poll, "id", "") or ""


@dataclass
class PollAnswerResult:
    known: bool  # опрос наш
    ask_custom: bool = False  # только что выбрали «Свой вариант» — надо попросить текст
    user_id: int | None = None
    broadcast_id: int | None = None


@sync_to_async
def record_poll_answer(poll_id: str, option_ids: list[int]) -> PollAnswerResult:
    """Голос из Telegram.

    Пустой `option_ids` — человек отозвал голос. Кто голосовал, берём из
    доставки: этот экземпляр опроса уходил ровно одному человеку.
    """
    delivery = (
        BroadcastDelivery.objects.filter(poll_id=poll_id)
        .exclude(poll_id="")
        .select_related("broadcast")
        .first()
    )
    if delivery is None:
        return PollAnswerResult(known=False)
    result = PollAnswerResult(known=True, user_id=delivery.user_id, broadcast_id=delivery.broadcast_id)
    vote = BroadcastPollVote.objects.filter(
        broadcast_id=delivery.broadcast_id, user_id=delivery.user_id
    ).first()

    if not option_ids:
        if vote is not None and vote.custom_status != BroadcastPollVote.CUSTOM_ANSWERED:
            vote.delete()
        elif vote is not None:
            # Текст «своего варианта» уже у нас — голос сняли, ответ оставляем.
            vote.option_ids = []
            vote.save(update_fields=["option_ids", "updated_at"])
        return result

    custom_id = delivery.broadcast.custom_option_id
    picked_custom = custom_id is not None and custom_id in option_ids
    if vote is None:
        vote = BroadcastPollVote(broadcast_id=delivery.broadcast_id, user_id=delivery.user_id)
    was_custom = custom_id is not None and custom_id in (vote.option_ids or [])
    vote.option_ids = list(option_ids)

    if picked_custom and not was_custom and vote.custom_status != BroadcastPollVote.CUSTOM_ANSWERED:
        vote.custom_status = BroadcastPollVote.CUSTOM_AWAITING
        result.ask_custom = True
    elif not picked_custom and vote.custom_status == BroadcastPollVote.CUSTOM_AWAITING:
        # Передумал до того, как написал, — ловить его следующее сообщение незачем.
        vote.custom_status = ""
    vote.save()
    return result


@sync_to_async
def awaiting_custom_answer(user_id: int) -> BroadcastPollVote | None:
    """Ждём ли от человека «свой вариант». Если опросов несколько — последний."""
    return (
        BroadcastPollVote.objects.filter(
            user_id=user_id, custom_status=BroadcastPollVote.CUSTOM_AWAITING
        )
        .select_related("broadcast")
        .order_by("-updated_at")
        .first()
    )


@sync_to_async
def save_custom_answer(vote_id: int, text: str) -> None:
    BroadcastPollVote.objects.filter(pk=vote_id).update(
        custom_status=BroadcastPollVote.CUSTOM_ANSWERED, custom_text=text, updated_at=now()
    )


@sync_to_async
def decline_custom_answer(user_id: int, broadcast_id: int) -> bool:
    """False — отказываться уже не от чего: человек успел ответить или передумал."""
    return bool(BroadcastPollVote.objects.filter(
        user_id=user_id, broadcast_id=broadcast_id, custom_status=BroadcastPollVote.CUSTOM_AWAITING
    ).update(custom_status=BroadcastPollVote.CUSTOM_DECLINED, updated_at=now()))


def custom_prompt_keyboard(broadcast_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from bot import texts
    from bot.keyboards.factories import PollCustomCallback

    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=texts.POLL_CUSTOM_DECLINE_BUTTON,
        callback_data=PollCustomCallback(broadcast_id=broadcast_id).pack(),
    )]])


def menu_keyboard():
    """«В меню», которая снимает клавиатуру и присылает меню новым сообщением."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=MENU_BUTTON_TEXT, callback_data=MENU_CALLBACK)
    ]])


async def run(bot, broadcast_id: int) -> dict:
    broadcast = await sync_to_async(Broadcast.objects.get)(pk=broadcast_id)
    targets = await sync_to_async(recipients)(broadcast)

    await sync_to_async(Broadcast.objects.filter(pk=broadcast_id).update)(
        status=BroadcastStatusEnum.SENDING, started_at=now()
    )
    shared_keyboard = None if broadcast.with_referral_buttons else keyboard_for(broadcast)
    sent = failed = 0

    for user in targets:
        keyboard = shared_keyboard or keyboard_for(broadcast, user.pk)
        delivered, error, poll_id = await _send_one(bot, user.pk, broadcast, keyboard)
        await sync_to_async(BroadcastDelivery.objects.update_or_create)(
            broadcast=broadcast,
            user=user,
            defaults={"is_delivered": delivered, "error": error, "poll_id": poll_id},
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
