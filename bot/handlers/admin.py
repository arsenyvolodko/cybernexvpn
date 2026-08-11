"""Служебные команды. Видит и может только администратор.

Сейчас здесь одно: проверка канала оплаты. Она отвечает на вопрос, который
иначе можно выяснить лишь настоящей покупкой, — доходит ли до нас
подтверждение от ЮKassa и каким путём.
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from django.conf import settings

from bot.services import start_test_payment
from nexvpn.models import NexUser

logger = logging.getLogger(__name__)

router = Router(name="admin")

# Фильтр на весь роутер: чужому эти команды недоступны, причём молча —
# отвечать «нет прав» значит подтверждать, что команда существует.
router.message.filter(F.from_user.id == settings.TG_ADMIN_USER_ID)

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
