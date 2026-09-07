"""Сообщение об успешном подключении уходит с картинкой.

Отдельный файл, потому что вторая точка отправки — синхронная, на httpx: её
дёргает вебхук панели из веб-процесса, а не бот. Логика должна совпадать с той,
что в `bot.device_watch`, иначе человек увидит разное в зависимости от того,
кто первым узнал о подключении — поллер или вебхук.
"""

import json

import httpx
import pytest
from django.test import override_settings

from bot import media, notify

pytestmark = pytest.mark.django_db


class FakeResponse:
    def __init__(self, status_code=200, text="{}"):
        self.status_code = status_code
        self.text = text


class Recorder:
    """Подменяет httpx.post и запоминает, что и куда ушло."""

    def __init__(self, fail_methods=()):
        self.calls = []
        self.fail_methods = set(fail_methods)

    def __call__(self, url, **kwargs):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, kwargs))
        if method in self.fail_methods:
            return FakeResponse(400, '{"description": "нельзя"}')
        return FakeResponse()

    @property
    def methods(self):
        return [method for method, _ in self.calls]

    def payload(self, method):
        for name, kwargs in self.calls:
            if name == method:
                return kwargs
        raise AssertionError(f"{method} не вызывался, были: {self.methods}")


@pytest.fixture
def post(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(httpx, "post", recorder)
    return recorder


@override_settings(TG_BOT_TOKEN="test-token")
def test_sends_photo_and_removes_the_old_screen(post):
    notify.notify_device_connected(chat_id=100, message_id=5, device_title="iPhone 15")

    assert post.methods == ["sendPhoto", "deleteMessage"]
    assert "iPhone 15" in post.payload("sendPhoto")["data"]["caption"]
    assert post.payload("deleteMessage")["json"]["message_id"] == 5


@override_settings(TG_BOT_TOKEN="test-token")
def test_markup_goes_as_json_string(post):
    """В multipart вложенный объект не передать — только строкой."""
    notify.notify_device_connected(chat_id=100, message_id=5, device_title="iPhone 15")

    markup = post.payload("sendPhoto")["data"]["reply_markup"]
    assert isinstance(markup, str)
    assert "inline_keyboard" in json.loads(markup)


@override_settings(TG_BOT_TOKEN="test-token")
def test_falls_back_to_editing_text(monkeypatch):
    recorder = Recorder(fail_methods={"sendPhoto"})
    monkeypatch.setattr(httpx, "post", recorder)

    notify.notify_device_connected(chat_id=100, message_id=5, device_title="iPhone 15")

    assert recorder.methods == ["sendPhoto", "editMessageText"]
    assert "deleteMessage" not in recorder.methods, "старый экран удалять нечем — фото не ушло"


@override_settings(TG_BOT_TOKEN="test-token")
def test_falls_back_to_a_new_message_if_nothing_else_worked(monkeypatch):
    recorder = Recorder(fail_methods={"sendPhoto", "editMessageText"})
    monkeypatch.setattr(httpx, "post", recorder)

    notify.notify_device_connected(chat_id=100, message_id=5, device_title="iPhone 15")

    assert recorder.methods == ["sendPhoto", "editMessageText", "sendMessage"]


@override_settings(TG_BOT_TOKEN="test-token")
def test_missing_file_does_not_break_the_notification(monkeypatch, post, tmp_path):
    monkeypatch.setattr(media, "CONNECT_SUCCESS_PHOTO", tmp_path / "нет.jpg")

    notify.notify_device_connected(chat_id=100, message_id=5, device_title="iPhone 15")

    assert post.methods == ["editMessageText"]


# --- кнопка «Хорошо» под картинкой ---


class FakeChat:
    def __init__(self, cid):
        self.id = cid


class FakeMessage:
    def __init__(self, chat_id=100, message_id=7):
        self.chat = FakeChat(chat_id)
        self.message_id = message_id
        self.markup_calls = []
        self.answers = []
        self.edited_text = None

    async def edit_reply_markup(self, reply_markup=None):
        self.markup_calls.append(reply_markup)

    async def edit_text(self, *args, **kwargs):
        self.edited_text = (args, kwargs)

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))
        return self


class FakeCall:
    def __init__(self, message):
        self.message = message
        self.answered = False

    async def answer(self):
        self.answered = True


def test_the_keyboard_is_a_single_ok_button():
    from bot.keyboards import keyboards

    keyboard = keyboards.connected()

    from bot.keyboards.storage import ButtonsStorage

    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert len(buttons) == 1
    assert buttons[0].text == ButtonsStorage.CONNECT_OK.text
    assert buttons[0].text.startswith("Хорошо")


def test_the_ok_button_is_handled():
    """Иначе нажатие молча улетит в legacy-заглушку «кнопка устарела»."""
    from types import SimpleNamespace

    from bot.handlers.broadcast import router
    from bot.keyboards.storage import ButtonsStorage

    event = SimpleNamespace(data=ButtonsStorage.CONNECT_OK.callback)
    matched = any(
        all(flt.callback(event) for flt in (handler.filters or []))
        for handler in router.callback_query.handlers
    )
    assert matched, "«Хорошо» никто не обрабатывает"


def test_ok_hides_the_keyboard_and_sends_the_menu():
    from asgiref.sync import async_to_sync

    from bot import texts
    from bot.handlers.broadcast import handle_connect_ok

    message = FakeMessage()
    call = FakeCall(message)

    async_to_sync(handle_connect_ok)(call)

    assert message.markup_calls == [None], "клавиатура должна исчезнуть"
    assert len(message.answers) == 1
    assert message.answers[0][0] == texts.MAIN_MENU
    assert message.edited_text is None, "картинку с подписью трогать нельзя"


def test_connect_hint_is_gone():
    """Подсказки по приложениям выпилены — шаблон больше ничего не подставляет."""
    from bot import apps_catalog, texts

    assert "{" not in texts.CONNECT_ADD_SUBSCRIPTION
    assert not hasattr(apps_catalog.PlatformGuide, "connect_hint")
    assert "connect_hint" not in apps_catalog.PlatformGuide.__dataclass_fields__
