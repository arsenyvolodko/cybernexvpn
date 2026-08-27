"""Служебные команды. Видит и может только администратор.

Две штуки. Проверка канала оплаты отвечает на вопрос, который иначе можно
выяснить лишь настоящей покупкой, — доходит ли до нас подтверждение от ЮKassa
и каким путём. Рассылка `/send` — то же, что и в админке, но из телефона: там
нужно открыть браузер и писать разметку руками, здесь достаточно написать сообщение
как обычно, с вложением, и увидеть его глазами получателя перед отправкой.
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from bot import broadcast as broadcast_service
from bot import texts
from bot.keyboards.factories import BroadcastCallback
from bot.services import start_test_payment
from nexvpn.enums import BroadcastAudienceEnum, BroadcastStatusEnum
from nexvpn.models import Broadcast, NexUser

logger = logging.getLogger(__name__)

router = Router(name="admin")

# Фильтр на весь роутер: чужому эти команды недоступны, причём молча —
# отвечать «нет прав» значит подтверждать, что команда существует.
router.message.filter(F.from_user.id == settings.TG_ADMIN_USER_ID)
router.callback_query.filter(F.from_user.id == settings.TG_ADMIN_USER_ID)

TEST_AMOUNT = 5


@router.message(Command("testpay"))
async def handle_test_payment(message: Message, user: NexUser) -> None:
    try:
        url = await start_test_payment(user, TEST_AMOUNT, settings.TG_BOT_URL)
    except Exception as exc:
        logger.exception("Проверочный платёж не создался")
        await message.answer(f"Не создался платёж: {exc}")
        return

    await message.answer(
        f"Проверочный платёж на {TEST_AMOUNT} ₽.\n\n"
        f"Оплати — и я пришлю, каким путём пришло подтверждение и за сколько секунд. "
        f"Ничего начислено не будет.\n\n{url}",
        disable_web_page_preview=True,
    )


# --- рассылка из бота ---


class SendForm(StatesGroup):
    waiting_content = State()


def _confirm_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Да, отправить",
            callback_data=BroadcastCallback(broadcast_id=broadcast_id, action="send").pack(),
            style="success",
        ),
        InlineKeyboardButton(
            text="Нет",
            callback_data=BroadcastCallback(broadcast_id=broadcast_id, action="drop").pack(),
        ),
    ]])


def _create_draft(message: Message) -> Broadcast:
    """Черновик по сообщению администратора.

    `text` здесь — только чтобы рассылку можно было опознать в админке: сама
    отправка идёт копией исходного сообщения и в это поле не заглядывает.
    """
    try:
        preview = message.html_text
    except TypeError:
        # Вложение без подписи — текста у сообщения нет вовсе.
        preview = f"[{message.content_type} без подписи]"

    return Broadcast.objects.create(
        title=f"Из бота, {timezone.localtime():%d.%m.%Y %H:%M}",
        text=preview,
        audience=BroadcastAudienceEnum.ALL,
        status=BroadcastStatusEnum.DRAFT,
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
    )


def _recipients_count(broadcast: Broadcast) -> int:
    return len(broadcast_service.recipients(broadcast))


@router.message(Command("send"))
async def handle_send(message: Message, state: FSMContext) -> None:
    await state.set_state(SendForm.waiting_content)
    await message.answer(texts.ADMIN_SEND_PROMPT)


@router.message(Command("cancel"), SendForm.waiting_content)
async def handle_send_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.ADMIN_SEND_CANCELLED)


@router.message(SendForm.waiting_content)
async def handle_send_content(message: Message, state: FSMContext) -> None:
    if message.media_group_id:
        # Состояние оставляем: человек просто пришлёт то же самое одним файлом.
        await message.answer(texts.ADMIN_SEND_ALBUM)
        return

    if message.content_type == "text" and not (message.text or "").strip():
        await message.answer(texts.ADMIN_SEND_EMPTY)
        return

    await state.clear()
    broadcast = await sync_to_async(_create_draft)(message)

    # Предпросмотр — копией, тем же способом, каким уйдёт людям. Пересказать
    # словами тут нельзя: как отрисуется подпись под вложением, видно только
    # на настоящем сообщении.
    await message.bot.copy_message(
        chat_id=message.chat.id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )
    await message.answer(texts.ADMIN_SEND_PREVIEW)

    count = await sync_to_async(_recipients_count)(broadcast)
    await message.answer(
        texts.ADMIN_SEND_CONFIRM.format(count=count),
        reply_markup=_confirm_keyboard(broadcast.pk),
    )


async def _drop_confirm_keyboard(call: CallbackQuery) -> None:
    """Снять кнопки, чтобы по ним не нажали второй раз."""
    if call.message is None:
        return
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.debug("Не удалось снять клавиатуру подтверждения", exc_info=True)


async def _reply(call: CallbackQuery, text: str) -> None:
    """Ответить на нажатие.

    У кнопки старше двух суток Telegram не отдаёт исходное сообщение — тогда
    пишем в чат напрямую, чтобы человек не остался без ответа.
    """
    if call.message is not None:
        await call.message.answer(text)
    else:
        await call.bot.send_message(call.from_user.id, text)


@router.callback_query(BroadcastCallback.filter(F.action == "send"))
async def handle_send_confirm(call: CallbackQuery, callback_data: BroadcastCallback) -> None:
    await call.answer()
    await _drop_confirm_keyboard(call)

    broadcast = await sync_to_async(
        Broadcast.objects.filter(pk=callback_data.broadcast_id).first
    )()
    if broadcast is None or broadcast.status != BroadcastStatusEnum.DRAFT:
        # Черновик успели удалить или отправить с другого устройства. Повторная
        # отправка тремстам людям — не та ошибка, которую можно допустить.
        await _reply(call, texts.ADMIN_SEND_GONE)
        return

    count = await sync_to_async(_recipients_count)(broadcast)
    await _reply(call, texts.ADMIN_SEND_STARTED.format(count=count))

    from nexvpn.tasks import send_broadcast

    # В celery, а не здесь:Триста сообщений идут около минуты, и обработчик
    # бота столько занимать нельзя.
    await sync_to_async(send_broadcast.delay)(broadcast.pk)


@router.callback_query(BroadcastCallback.filter(F.action == "drop"))
async def handle_send_drop(call: CallbackQuery, callback_data: BroadcastCallback) -> None:
    await call.answer()
    await _drop_confirm_keyboard(call)
    await sync_to_async(
        Broadcast.objects.filter(
            pk=callback_data.broadcast_id, status=BroadcastStatusEnum.DRAFT
        ).delete
    )()
    await _reply(call, texts.ADMIN_SEND_DROPPED)
