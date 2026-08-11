"""Ожидание подключения устройства после «Добавить подписку»."""

import asyncio

import pytest

from bot import device_watch
from bot.screen_state import mark_screen
from bot.services import Device


def device(hwid: str, title: str = "iPhone 15") -> Device:
    return Device(hwid=hwid, token=hwid[:16], title=title, platform="iOS", last_seen=None)


class FakeBot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(device_watch, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(device_watch, "POLL_TIMEOUT_SECONDS", 0.2)


@pytest.fixture(autouse=True)
def watch_is_ours(monkeypatch):
    """По умолчанию замок достаётся поллеру; отдельный тест проверяет обратное."""
    async def claim(_user):
        return True

    monkeypatch.setattr(device_watch, "claim_connection_watch", claim)


def run(coro):
    return asyncio.run(coro)


def patch_devices(monkeypatch, sequence):
    """Панель отдаёт по очереди то, что перечислено в sequence."""
    calls = iter(sequence)

    async def fake(_user):
        try:
            return next(calls)
        except StopIteration:
            return sequence[-1]

    monkeypatch.setattr(device_watch, "list_devices", fake)


class FakeUser:
    pk = 1


def test_notifies_when_a_new_device_appears(monkeypatch):
    bot = FakeBot()
    mark_screen(100, "connect:ios:5")
    patch_devices(monkeypatch, [[], [device("NEW")]])

    run(device_watch.watch_for_new_device(bot, 100, 5, FakeUser(), set(), "connect:ios:5"))

    assert len(bot.edits) == 1
    assert "iPhone 15" in bot.edits[0]["text"]


def test_ignores_devices_that_were_already_there(monkeypatch):
    """У человека уже могли быть подключённые устройства — это не успех."""
    bot = FakeBot()
    mark_screen(100, "connect:ios:5")
    patch_devices(monkeypatch, [[device("OLD")], [device("OLD")]])

    run(device_watch.watch_for_new_device(bot, 100, 5, FakeUser(), {"OLD"}, "connect:ios:5"))

    assert bot.edits == []


def test_does_not_overwrite_a_screen_the_user_moved_to(monkeypatch):
    """Главная защита: человек ушёл в другой раздел — его экран трогать нельзя."""
    bot = FakeBot()
    mark_screen(100, "faq")  # ушёл, пока ждали
    patch_devices(monkeypatch, [[device("NEW")]])

    run(device_watch.watch_for_new_device(bot, 100, 5, FakeUser(), set(), "connect:ios:5"))

    assert bot.edits == []


def test_stays_silent_if_the_webhook_was_first(monkeypatch):
    """Замок уже забрал вебхук — второе сообщение об одном событии не нужно."""
    bot = FakeBot()
    mark_screen(100, "connect:ios:5")
    patch_devices(monkeypatch, [[device("NEW")]])

    async def already_taken(_user):
        return False

    monkeypatch.setattr(device_watch, "claim_connection_watch", already_taken)

    run(device_watch.watch_for_new_device(bot, 100, 5, FakeUser(), set(), "connect:ios:5"))

    assert bot.edits == []


def test_survives_panel_outage(monkeypatch):
    bot = FakeBot()
    mark_screen(100, "connect:ios:5")

    async def broken(_user):
        raise RuntimeError("панель лежит")

    monkeypatch.setattr(device_watch, "list_devices", broken)

    run(device_watch.watch_for_new_device(bot, 100, 5, FakeUser(), set(), "connect:ios:5"))

    assert bot.edits == []


def test_gives_up_after_timeout(monkeypatch):
    bot = FakeBot()
    mark_screen(100, "connect:ios:5")
    patch_devices(monkeypatch, [[]])

    run(device_watch.watch_for_new_device(bot, 100, 5, FakeUser(), set(), "connect:ios:5"))

    assert bot.edits == []
