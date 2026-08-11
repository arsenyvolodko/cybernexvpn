import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot import texts
from bot.faq_topics import BY_KEY, TOPICS
from bot.handlers.common import render
from bot.keyboards import ButtonsStorage, keyboards
from bot.keyboards.factories import FaqCallback

logger = logging.getLogger(__name__)

router = Router(name="faq")


@router.callback_query(F.data == ButtonsStorage.FAQ_SUPPORT.callback)
async def handle_faq(call: CallbackQuery) -> None:
    await call.answer()
    await render(call, texts.FAQ, keyboards.faq(TOPICS))


@router.callback_query(FaqCallback.filter())
async def handle_topic(call: CallbackQuery, callback_data: FaqCallback) -> None:
    topic = BY_KEY.get(callback_data.topic)
    if topic is None:
        await call.answer()
        await render(call, texts.FAQ, keyboards.faq(TOPICS))
        return
    await call.answer()
    await render(call, topic.body, keyboards.faq_section())

