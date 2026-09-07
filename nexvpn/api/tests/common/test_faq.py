"""Разделы FAQ, в том числе раздел с картинкой.

Картинка тут не украшение: текст про выбор туннеля ссылается на кнопку «см
фото», и без неё раздел теряет смысл. Поэтому проверяется не только то, что
фото уходит, но и то, что из такого экрана можно уйти назад — сообщение с фото
нельзя отредактировать в текст, и навигация об это ломается легче всего.
"""

import pytest
from asgiref.sync import async_to_sync

from bot import faq_topics, media, texts
from bot.handlers.faq import handle_faq, handle_topic
from bot.keyboards.factories import FaqCallback

pytestmark = pytest.mark.django_db


class FakeChat:
    def __init__(self, cid=100):
        self.id = cid


class FakeMessage:
    def __init__(self, has_photo=False, photo_fails=False):
        self.chat = FakeChat()
        self.message_id = 7
        self.photo = [object()] if has_photo else None
        self.document = None
        self.video = None
        self.photo_fails = photo_fails
        self.edits = []
        self.answers = []
        self.photos = []
        self.deleted = False

    async def edit_text(self, text, reply_markup=None):
        self.edits.append(text)

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)
        return FakeMessage()

    async def answer_photo(self, photo, caption=None, reply_markup=None):
        if self.photo_fails:
            raise RuntimeError("Telegram отказал")
        self.photos.append(caption)
        return FakeMessage(has_photo=True)

    async def edit_reply_markup(self, reply_markup=None):
        pass

    async def delete(self):
        self.deleted = True


class FakeCall:
    def __init__(self, message):
        self.message = message

    async def answer(self):
        pass


def show(topic_key: str, message: FakeMessage) -> None:
    async_to_sync(handle_topic)(FakeCall(message), FaqCallback(topic=topic_key))


def test_photo_topics_fit_the_caption_limit():
    """Подпись к медиа — 1024 символа против 4096 у текста.

    Раздел, переросший лимит, не отправится вовсе, и это заметит уже человек.
    """
    for topic in faq_topics.TOPICS:
        if topic.photo is None:
            continue
        assert topic.photo.exists(), f"{topic.key}: файла нет"
        assert len(topic.body) <= media.CAPTION_LIMIT, f"{topic.key}: подпись слишком длинная"


def test_the_tunnel_topic_is_in_the_list():
    keys = [topic.key for topic in faq_topics.TOPICS]
    assert "best_tunnel" in keys
    assert faq_topics.BY_KEY["best_tunnel"].photo is not None


def test_topic_with_photo_goes_as_a_photo():
    message = FakeMessage()

    show("best_tunnel", message)

    assert len(message.photos) == 1
    assert "пинг" in message.photos[0].lower()
    assert message.edits == [], "текстовую правку тут применить нельзя"
    assert message.deleted, "прежний экран должен уйти, иначе останется мёртвая клавиатура"


def test_topic_without_photo_is_still_edited_in_place():
    """Обычные разделы не должны начать плодить сообщения из-за новой ветки."""
    message = FakeMessage()

    show("devices", message)

    assert len(message.edits) == 1
    assert message.photos == []
    assert not message.deleted


def test_photo_topic_falls_back_to_text():
    message = FakeMessage(photo_fails=True)

    show("best_tunnel", message)

    assert message.photos == []
    assert len(message.answers) == 1, "раздел должен показаться хотя бы текстом"


def test_back_from_a_photo_screen_sends_a_new_message():
    """Сообщение с фото не отредактировать в текст — список FAQ уезжает в новое."""
    message = FakeMessage(has_photo=True)

    async_to_sync(handle_faq)(FakeCall(message))

    assert message.edits == []
    assert message.answers == [texts.FAQ]
    assert message.deleted
