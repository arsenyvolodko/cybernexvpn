"""Заслон «подпишись на канал».

Два риска, ради которых написаны эти тесты. Первый — заслон легко обойти, если
закрыть только `/start`. Второй, куда неприятнее: при сбое Telegram проверка
может запереть **всех** новых пользователей, и регистрации встанут молча.
"""

import pytest
from aiogram.exceptions import TelegramForbiddenError
from asgiref.sync import async_to_sync

from bot import channel
from nexvpn.api.tests.factories import NexUserFactory

pytestmark = pytest.mark.django_db


class FakeBot:
    def __init__(self, status=None, error=None):
        self.status = status
        self.error = error
        self.calls = []

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        if self.error:
            raise self.error
        return type("Member", (), {"status": self.status})()


def subscribed(bot, user_id=1):
    return async_to_sync(channel.is_subscribed)(bot, user_id)


@pytest.mark.parametrize("status", ["creator", "administrator", "member", "restricted"])
def test_member_statuses_count_as_subscribed(status):
    assert subscribed(FakeBot(status=status)) is True


@pytest.mark.parametrize("status", ["left", "kicked"])
def test_non_member_statuses_do_not(status):
    assert subscribed(FakeBot(status=status)) is False


def test_api_failure_lets_the_person_through():
    """Бота вывели из админов канала — регистрации не должны встать.

    Пропустить одного неподписавшегося дешевле, чем молча потерять всех новых.
    """
    bot = FakeBot(error=TelegramForbiddenError(method=None, message="bot is not a member"))

    assert subscribed(bot) is True


def test_legacy_user_is_never_asked():
    """У легаси доступ уже оплачен — условие задним числом было бы нечестным."""
    user = NexUserFactory(is_legacy=True, joined_channel=False)

    assert channel.gate_required_for(user) is False


def test_new_user_is_asked():
    user = NexUserFactory(is_legacy=False, joined_channel=False)

    assert channel.gate_required_for(user) is True


def test_confirmed_user_is_not_asked_again():
    user = NexUserFactory(is_legacy=False, joined_channel=True)

    assert channel.gate_required_for(user) is False


def test_empty_channel_setting_disables_the_gate(settings):
    """Рубильник на случай, если бота выведут из канала совсем."""
    settings.TG_CHANNEL_USERNAME = ""
    user = NexUserFactory(is_legacy=False, joined_channel=False)

    assert channel.gate_required_for(user) is False


def test_gate_keyboard_has_channel_link_and_check(settings):
    settings.TG_CHANNEL_URL = "https://t.me/cybernexvpn"

    rows = channel.gate_keyboard().inline_keyboard

    assert rows[0][0].url == "https://t.me/cybernexvpn"
    assert rows[1][0].callback_data == channel.CHECK_CALLBACK


def test_gate_middleware_is_registered_after_user():
    """Порядок важен: заслону нужен уже подтянутый пользователь.

    Читаем исходник, а не собираем диспетчер: роутеры бота — синглтоны, и
    второй сборкой их не поднять.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[4] / "bot" / "main.py").read_text()
    assert "UserMiddleware()" in source and "ChannelGateMiddleware()" in source
    assert source.index("UserMiddleware()") < source.index("ChannelGateMiddleware()")


@pytest.mark.parametrize(
    "text,expected",
    [("/start", True), ("/start 123456", True), ("/start@CyberNexVpnBot", True),
     ("привет", False), ("/menu", False), ("", False), ("/startle", False)],
)
def test_start_is_recognised(text, expected):
    from bot.middlewares.channel_gate import is_start_command

    assert is_start_command(text) is expected


def test_gate_middleware_actually_runs():
    """Прогоняем middleware целиком.

    Первая версия падала на `CommandStart()(event)` — фильтру aiogram нужен
    ещё и бот. В бою это закрывало вход **всем** новичкам: сообщение об ошибке
    в лог, человеку — тишина. Проверки «зарегистрирован ли middleware» такое
    не ловят, нужен настоящий вызов.
    """
    import asyncio

    from aiogram.types import Chat, Message, User as TgUser

    from bot.middlewares.channel_gate import ChannelGateMiddleware

    user = NexUserFactory(is_legacy=False, joined_channel=False)
    sent = []

    class FakeMessage(Message):
        async def answer(self, text, **kwargs):
            sent.append(text)

    def make(text):
        return FakeMessage(
            message_id=1, date=0, chat=Chat(id=user.pk, type="private"),
            from_user=TgUser(id=user.pk, is_bot=False, first_name="t"), text=text,
        )

    passed = []

    async def handler(event, data):
        passed.append(event.text)

    middleware = ChannelGateMiddleware()
    asyncio.run(middleware(handler, make("/start"), {"user": user}))
    asyncio.run(middleware(handler, make("привет"), {"user": user}))

    assert passed == ["/start"], "/start обязан доходить до хендлера"
    assert len(sent) == 1 and "канал" in sent[0], "остальное упирается в заслон"
