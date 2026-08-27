"""Сценарий /send: от команды до постановки рассылки в очередь."""
import pytest
from asgiref.sync import async_to_sync

from bot.handlers import admin as admin_handlers
from bot.keyboards.factories import BroadcastCallback
from nexvpn.api.tests.factories import NexUserFactory
from nexvpn.enums import BroadcastStatusEnum
from nexvpn.models import Broadcast

pytestmark = pytest.mark.django_db


class FakeState:
    def __init__(self): self.state = None
    async def set_state(self, s): self.state = s
    async def clear(self): self.state = None


class FakeBot:
    def __init__(self): self.copies = []; self.messages = []
    async def copy_message(self, chat_id, from_chat_id, message_id, **kw):
        self.copies.append((chat_id, from_chat_id, message_id))
    async def send_message(self, chat_id, text, **kw):
        self.messages.append((chat_id, text))


class FakeChat:
    def __init__(self, cid): self.id = cid


class FakeMessage:
    def __init__(self, bot, chat_id=506, message_id=11, text="Привет", content_type="text",
                 media_group_id=None, html=None):
        self.bot = bot; self.chat = FakeChat(chat_id); self.message_id = message_id
        self.text = text; self.content_type = content_type
        self.media_group_id = media_group_id
        self._html = html
        self.answers = []
    @property
    def html_text(self):
        if self._html is None and self.text is None:
            raise TypeError("no text")
        return self._html if self._html is not None else self.text
    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup)); return self


class FakeCall:
    def __init__(self, bot, message): self.bot = bot; self.message = message
    async def answer(self): pass
    @property
    def from_user(self): raise AssertionError("не должно понадобиться")


def test_full_flow_creates_draft_previews_and_queues(monkeypatch):
    NexUserFactory(); NexUserFactory()
    bot = FakeBot(); state = FakeState()

    msg = FakeMessage(bot)
    async_to_sync(admin_handlers.handle_send)(msg, state)
    assert state.state == admin_handlers.SendForm.waiting_content
    assert "Пришли сообщение" in msg.answers[0][0]

    content = FakeMessage(bot, message_id=99, text="<b>Новость</b>")
    async_to_sync(admin_handlers.handle_send_content)(content, state)

    draft = Broadcast.objects.get()
    assert draft.status == BroadcastStatusEnum.DRAFT
    assert (draft.source_chat_id, draft.source_message_id) == (506, 99)
    assert draft.is_copied

    # предпросмотр — копией в тот же чат
    assert bot.copies == [(506, 506, 99)]
    texts_sent = [a[0] for a in content.answers]
    assert "увидят люди" in texts_sent[0]
    assert "Получателей: 2" in texts_sent[1]
    assert content.answers[1][1] is not None  # клавиатура да/нет

    queued = []
    monkeypatch.setattr("nexvpn.tasks.send_broadcast.delay", lambda pk: queued.append(pk))

    call = FakeCall(bot, FakeMessage(bot))
    async_to_sync(admin_handlers.handle_send_confirm)(
        call, BroadcastCallback(broadcast_id=draft.pk, action="send"))
    assert queued == [draft.pk]


def test_album_is_refused_and_state_kept():
    bot = FakeBot(); state = FakeState()
    state.state = admin_handlers.SendForm.waiting_content
    msg = FakeMessage(bot, media_group_id="123", content_type="photo")
    async_to_sync(admin_handlers.handle_send_content)(msg, state)
    assert Broadcast.objects.count() == 0
    assert "Альбом" in msg.answers[0][0]
    assert state.state == admin_handlers.SendForm.waiting_content


def test_attachment_without_caption_gets_readable_title():
    NexUserFactory()
    bot = FakeBot(); state = FakeState()
    msg = FakeMessage(bot, text=None, content_type="photo")
    async_to_sync(admin_handlers.handle_send_content)(msg, state)
    draft = Broadcast.objects.get()
    assert draft.text == "[photo без подписи]"


def test_drop_deletes_draft():
    bot = FakeBot()
    draft = Broadcast.objects.create(title="t", text="x", source_chat_id=1, source_message_id=2)
    call = FakeCall(bot, FakeMessage(bot))
    async_to_sync(admin_handlers.handle_send_drop)(
        call, BroadcastCallback(broadcast_id=draft.pk, action="drop"))
    assert Broadcast.objects.count() == 0


def test_already_sent_draft_is_not_sent_twice(monkeypatch):
    NexUserFactory()
    bot = FakeBot()
    draft = Broadcast.objects.create(title="t", text="x", source_chat_id=1, source_message_id=2,
                                     status=BroadcastStatusEnum.SENT)
    queued = []
    monkeypatch.setattr("nexvpn.tasks.send_broadcast.delay", lambda pk: queued.append(pk))
    call = FakeCall(bot, FakeMessage(bot))
    async_to_sync(admin_handlers.handle_send_confirm)(
        call, BroadcastCallback(broadcast_id=draft.pk, action="send"))
    assert queued == []
