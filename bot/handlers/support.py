"""Поддержка внутри бота.

Никаких внешних аккаунтов: человек пишет прямо в этот чат, обращение уходит
администратору личным сообщением от бота. Так у человека не появляется повода
уходить из бота, а мы сразу видим, кто пишет и что у него с подпиской.

Ответ идёт обратно тем же путём: администратор **отвечает на сообщение** с
обращением, и бот пересылает текст человеку. Кому отвечать, определяется по
id, который лежит в самом обращении, — отдельная таблица для этого не нужна,
а значит нечему и рассинхронизироваться.
"""

import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from django.conf import settings
from django.utils import timezone

from bot import texts
from bot.handlers.common import render
from bot.keyboards import ButtonsStorage, keyboards
from bot.services import get_subscription_view, list_devices
from nexvpn.models import NexUser

logger = logging.getLogger(__name__)

router = Router(name="support")

USER_ID_PATTERN = re.compile(r"^id:\s*(\d+)$", re.MULTILINE)
MAX_LENGTH = 3000


class SupportForm(StatesGroup):
    waiting = State()


@router.callback_query(F.data == ButtonsStorage.SUPPORT.callback)
async def handle_support(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(SupportForm.waiting)
    await render(call, texts.SUPPORT, keyboards.faq_section())
    # Запоминаем экран, чтобы после отправки убрать с него кнопки: иначе
    # человек вернётся к нему и снова начнёт писать «в никуда».
    if call.message is not None:
        await state.update_data(prompt_message_id=call.message.message_id)


async def _describe_subscription(user: NexUser) -> str:
    """Контекст, без которого первый ответ поддержки всё равно был бы вопросом."""
    try:
        view = await get_subscription_view(user)
    except Exception:
        return "Подписка: не удалось получить"
    if not view.exists:
        return "Подписки нет"

    lines = [
        f"Тариф: {view.device_limit} устр., "
        f"{'активна' if view.is_active else 'ИСТЕКЛА'} до "
        f"{timezone.localtime(view.subscription.expires_at):%d.%m.%Y}"
    ]
    try:
        devices = await list_devices(user)
    except Exception:
        devices = None
    if devices is None:
        lines.append("Устройства: панель не ответила")
    else:
        lines.append(f"Устройства: {len(devices)} из {view.device_limit}")
        for device in devices[:5]:
            lines.append(f"  · {device.title}")
    if view.subscription.panel_status != "synced":
        lines.append(f"⚠️ Синхронизация с панелью: {view.subscription.panel_status}")
    return "\n".join(lines)


@router.message(SupportForm.waiting)
async def handle_ticket(message: Message, user: NexUser, state: FSMContext) -> None:
    text = (message.text or message.caption or "").strip()
    if not text:
        await message.answer(texts.SUPPORT_EMPTY)
        return

    data = await state.get_data()
    await state.clear()
    await _drop_prompt_keyboard(message, data.get("prompt_message_id"))

    if not settings.TG_ADMIN_USER_ID:
        logger.error("Обращение некуда отправить: не задан TG_ADMIN_USER_ID")
        await message.answer(texts.SUPPORT_SENT)
        return

    ticket = texts.SUPPORT_TICKET.format(
        name=message.from_user.full_name,
        username=f" (@{message.from_user.username})" if message.from_user.username else "",
        user_id=user.pk,
        when=timezone.localtime().strftime("%d.%m.%Y %H:%M"),
        subscription=await _describe_subscription(user),
        text=text[:MAX_LENGTH],
    )

    try:
        await message.bot.send_message(settings.TG_ADMIN_USER_ID, ticket)
    except Exception:
        # Человеку об этом знать незачем: обращение мы всё равно увидим в логе.
        logger.exception("Не удалось доставить обращение от %s: %s", user.pk, text[:200])

    await message.answer(texts.SUPPORT_SENT, reply_markup=keyboards.only_back())


async def _drop_prompt_keyboard(message: Message, prompt_message_id: int | None) -> None:
    if not prompt_message_id:
        return
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id, message_id=prompt_message_id, reply_markup=None
        )
    except Exception:
        logger.debug("Не удалось снять клавиатуру с экрана поддержки", exc_info=True)


@router.message(F.reply_to_message, F.from_user.id == settings.TG_ADMIN_USER_ID)
async def handle_admin_reply(message: Message) -> None:
    """Ответ администратора: он отвечает на обращение, бот несёт текст человеку."""
    # Именно .text: в html_text id обёрнут в <code>, и регулярка мимо.
    source = message.reply_to_message.text or ""
    match = USER_ID_PATTERN.search(source)
    if not match:
        return  # Не наше сообщение — пусть обрабатывают другие хендлеры.

    target = int(match.group(1))
    reply = (message.text or message.caption or "").strip()
    if not reply:
        return

    try:
        await message.bot.send_message(target, texts.SUPPORT_REPLY_HEADER.format(text=reply))
    except Exception:
        logger.warning("Не удалось доставить ответ пользователю %s", target, exc_info=True)
        await message.reply(texts.SUPPORT_REPLY_FAILED)
        return

    await message.reply(texts.SUPPORT_REPLY_SENT)
