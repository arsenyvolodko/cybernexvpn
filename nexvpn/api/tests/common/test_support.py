"""Обращение в поддержку: текст, фото, альбом — и ответ администратора реплаем.

Гоняем через настоящий диспетчер: альбом приходит несколькими апдейтами
параллельно, и ловушки тут именно в порядке и одновременности, которые
отдельным вызовом обработчика не проверить.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.methods import CopyMessage, SendMessage
from aiogram.types import CallbackQuery, Chat, Document, Message, MessageId, PhotoSize, Update, User
from asgiref.sync import async_to_sync

from bot.keyboards.storage import ButtonsStorage
from nexvpn.api.tests.factories import NexUserFactory

pytestmark = pytest.mark.django_db(transaction=True)

ADMIN, PERSON = 999, 555


def message(user_id, mid, *, text=None, photo=False, document=False, caption=None, album=None, reply_to=None):
    fields = dict(
        message_id=mid, date=datetime.now(timezone.utc), chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Вася <Пупкин>", username="vasya"),
        text=text, caption=caption, media_group_id=album, reply_to_message=reply_to,
    )
    if photo:
        fields["photo"] = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
    if document:
        fields["document"] = Document(file_id="d", file_unique_id="du")
    return Message(**fields)


class RecordingBot(Bot):
    def __init__(self):
        super().__init__(token="42:TEST")
        self.calls = []

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
        await asyncio.sleep(0)  # даём параллельным частям альбома перемешаться
        if isinstance(method, CopyMessage):
            return MessageId(message_id=700 + len(self.calls))
        if isinstance(method, SendMessage):
            return message(method.chat_id, 600 + len(self.calls), text=method.text)
        return True

    def sent(self, kind, chat_id):
        return [c for c in self.calls if isinstance(c, kind) and c.chat_id == chat_id]


@pytest.fixture
def world(settings, monkeypatch):
    from bot import main

    settings.TG_ADMIN_USER_ID = ADMIN
    NexUserFactory(id=PERSON, joined_channel=True)
    NexUserFactory(id=ADMIN, joined_channel=True)
    dispatcher, bot = main.build_dispatcher(), RecordingBot()

    def feed(*updates):
        async def go():
            await asyncio.gather(*(dispatcher.feed_update(bot, u) for u in updates))

        async_to_sync(go)()

    def open_support():
        call = CallbackQuery(
            id="1", from_user=User(id=PERSON, is_bot=False, first_name="Вася"), chat_instance="c",
            message=message(PERSON, 1, text="FAQ"), data=ButtonsStorage.SUPPORT.callback,
        )
        feed(Update(update_id=1, callback_query=call))
        bot.calls.clear()

    yield bot, feed, open_support
    _detach(dispatcher)
    from bot.handlers import support

    support._album_tickets.clear()


def _detach(router):
    """Роутеры бота — модульные и прикрепляются к корню навсегда. Открепляем,
    чтобы следующий тест (и соседние файлы) могли собрать диспетчер заново."""
    for child in list(router.sub_routers):
        _detach(child)
        child._parent_router = None
    router.sub_routers.clear()


def test_text_ticket_is_escaped(world):
    bot, feed, open_support = world
    open_support()

    feed(Update(update_id=2, message=message(PERSON, 10, text="не работает <script>")))

    ticket = bot.sent(SendMessage, ADMIN)[0].text
    assert "не работает &lt;script&gt;" in ticket and f"id: <code>{PERSON}</code>" in ticket
    assert "Вася &lt;Пупкин&gt;" in ticket
    assert bot.sent(SendMessage, PERSON)[0].text.startswith("Обращение отправлено")


def test_photo_with_caption_reaches_admin_attached_to_ticket(world):
    bot, feed, open_support = world
    open_support()

    feed(Update(update_id=2, message=message(PERSON, 10, photo=True, caption="вот ошибка")))

    ticket = bot.sent(SendMessage, ADMIN)[0]
    assert "вот ошибка" in ticket.text
    copy = bot.sent(CopyMessage, ADMIN)[0]
    assert copy.from_chat_id == PERSON and copy.message_id == 10
    assert copy.reply_to_message_id is not None, "вложение — ответом на обращение"
    assert f"id: <code>{PERSON}</code>" in copy.caption


def test_photo_or_screenshot_file_without_text_is_accepted(world):
    bot, feed, open_support = world
    open_support()

    feed(Update(update_id=2, message=message(PERSON, 10, document=True)))

    assert "без текста" in bot.sent(SendMessage, ADMIN)[0].text
    assert len(bot.sent(CopyMessage, ADMIN)) == 1


def test_album_is_one_ticket_with_every_photo(world):
    bot, feed, open_support = world
    open_support()

    feed(*[
        Update(update_id=10 + i, message=message(
            PERSON, 20 + i, photo=True, album="A1", caption="три скрина" if i == 0 else None,
        ))
        for i in range(3)
    ])

    tickets = bot.sent(SendMessage, ADMIN)
    assert len(tickets) == 1, "одно обращение на альбом"
    copies = bot.sent(CopyMessage, ADMIN)
    assert sorted(c.message_id for c in copies) == [20, 21, 22]
    assert len({c.reply_to_message_id for c in copies}) == 1 and copies[0].reply_to_message_id is not None
    assert len(bot.sent(SendMessage, PERSON)) == 1, "«отправлено» — один раз"

    # Следующее сообщение — уже не обращение.
    bot.calls.clear()
    feed(Update(update_id=30, message=message(PERSON, 40, text="спасибо")))
    assert bot.sent(SendMessage, ADMIN) == [] and bot.sent(CopyMessage, ADMIN) == []


def test_sticker_or_empty_is_asked_again(world):
    bot, feed, open_support = world
    open_support()

    feed(Update(update_id=2, message=message(PERSON, 10, text="")))

    assert bot.sent(SendMessage, ADMIN) == []
    assert "пришли фото" in bot.sent(SendMessage, PERSON)[0].text


def test_admin_can_reply_to_the_photo_itself(world):
    bot, feed, _ = world
    attachment = message(ADMIN, 700, photo=True, caption=f"📎 К обращению\nid: {PERSON}")

    feed(Update(update_id=5, message=message(ADMIN, 800, text="Попробуй другой туннель", reply_to=attachment)))

    reply = bot.sent(SendMessage, PERSON)[0].text
    assert "Попробуй другой туннель" in reply
