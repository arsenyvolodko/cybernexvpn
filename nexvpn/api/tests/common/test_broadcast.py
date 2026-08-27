"""Рассылка из админки.

Главное здесь — живучесть: на трёх сотнях получателей кто-то обязательно
заблокировал бота, а Telegram посреди пачки попросит притормозить. Ни одно из
этих событий не должно останавливать рассылку. Проверка на это важнее, чем на
счастливый путь: молча недоставленная половина обнаружится только по жалобам.
"""

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from asgiref.sync import async_to_sync

from bot import broadcast as broadcast_module
from nexvpn.api.tests.factories import NexUserFactory
from nexvpn.enums import BroadcastAudienceEnum, BroadcastStatusEnum
from nexvpn.models import Broadcast, BroadcastDelivery, LegacyMigrationRecord, PanelPresence

pytestmark = pytest.mark.django_db


class FakeBot:
    """Бот, которому можно назначить, на ком спотыкаться."""

    def __init__(self, fail_for=None, retry_once_for=None):
        self.sent: list[int] = []
        self.fail_for = set(fail_for or ())
        self.retry_once_for = set(retry_once_for or ())
        self.attempts: dict[int, int] = {}

    async def send_message(self, chat_id, text, reply_markup=None):
        self.attempts[chat_id] = self.attempts.get(chat_id, 0) + 1
        if chat_id in self.fail_for:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        if chat_id in self.retry_once_for and self.attempts[chat_id] == 1:
            raise TelegramRetryAfter(method=None, message="Too Many Requests", retry_after=0)
        self.sent.append(chat_id)


def make_broadcast(**kwargs):
    return Broadcast.objects.create(
        title=kwargs.pop("title", "Тест"),
        text=kwargs.pop("text", "<b>Привет</b>"),
        **kwargs,
    )


def legacy_user(device_count):
    user = NexUserFactory(is_legacy=True)
    LegacyMigrationRecord.objects.create(
        user=user, balance=0, device_count=device_count, days_granted=30,
        plan_device_limit=3, cutoff_date="2026-08-03",
    )
    return user


def run(bot, broadcast):
    return async_to_sync(broadcast_module.run)(bot, broadcast.pk)


def test_blocked_user_does_not_stop_the_rest():
    """Ради этого всё и написано: одна блокировка не должна съесть рассылку."""
    first, blocked, last = NexUserFactory(), NexUserFactory(), NexUserFactory()
    bot = FakeBot(fail_for=[blocked.pk])
    item = make_broadcast()

    result = run(bot, item)

    assert sorted(bot.sent) == sorted([first.pk, last.pk])
    assert result == {"sent": 2, "failed": 1}
    assert BroadcastDelivery.objects.get(user=blocked).error == "бот заблокирован"


def test_rate_limit_is_retried():
    user = NexUserFactory()
    bot = FakeBot(retry_once_for=[user.pk])

    result = run(bot, make_broadcast())

    assert result["sent"] == 1
    assert bot.attempts[user.pk] == 2


def test_resend_only_touches_those_who_missed_it():
    delivered, blocked = NexUserFactory(), NexUserFactory()
    item = make_broadcast()
    run(FakeBot(fail_for=[blocked.pk]), item)

    second = FakeBot()
    run(second, item)

    assert second.sent == [blocked.pk]


def test_gifted_and_converted_are_different_groups():
    gifted = legacy_user(device_count=0)
    converted = legacy_user(device_count=2)
    NexUserFactory(is_legacy=False)

    to_gifted = broadcast_module.recipients(
        make_broadcast(audience=BroadcastAudienceEnum.LEGACY_GIFTED)
    )
    to_converted = broadcast_module.recipients(
        make_broadcast(audience=BroadcastAudienceEnum.LEGACY_CONVERTED)
    )

    assert [u.pk for u in to_gifted] == [gifted.pk]
    assert [u.pk for u in to_converted] == [converted.pk]


def test_those_who_never_opened_the_bot_are_their_own_group():
    from django.utils.timezone import now

    never_came = NexUserFactory(is_legacy=True, activated_at=None)
    NexUserFactory(is_legacy=True, activated_at=now())

    targets = broadcast_module.recipients(
        make_broadcast(audience=BroadcastAudienceEnum.NOT_ACTIVATED)
    )

    assert [u.pk for u in targets] == [never_came.pk]


def test_never_connected_needs_a_presence_row():
    """Иначе объявление «у тебя не получилось» уедет тем, у кого всё работает.

    Пустая телеметрия — штатная ситуация: celery-beat в проде поднят не всегда.
    В этом случае группа обязана быть пустой, а не всеми подряд.
    """
    from django.utils.timezone import now

    stuck = NexUserFactory(activated_at=now())
    connected = NexUserFactory(activated_at=now())
    no_telemetry_yet = NexUserFactory(activated_at=now())
    PanelPresence.objects.create(user=stuck, first_connected_at=None)
    PanelPresence.objects.create(user=connected, first_connected_at=now())

    targets = broadcast_module.recipients(
        make_broadcast(audience=BroadcastAudienceEnum.NOT_CONNECTED)
    )

    assert [u.pk for u in targets] == [stuck.pk]
    assert no_telemetry_yet.pk not in [u.pk for u in targets]


def test_test_only_goes_to_the_admin_alone(settings):
    admin = NexUserFactory()
    settings.TG_ADMIN_USER_ID = admin.pk
    NexUserFactory()
    NexUserFactory()

    targets = broadcast_module.recipients(make_broadcast(test_only=True))

    assert [u.pk for u in targets] == [admin.pk]


def test_buttons_have_their_own_callbacks():
    """Свои, а не менюшные: менюшные правят сообщение, а объявление трогать нельзя."""
    keyboard = broadcast_module.keyboard_for(
        make_broadcast(with_connect_button=True, with_menu_button=True)
    )

    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert callbacks == [broadcast_module.CONNECT_CALLBACK, broadcast_module.MENU_CALLBACK]


@pytest.mark.parametrize(
    "callback",
    [broadcast_module.CONNECT_CALLBACK, broadcast_module.MENU_CALLBACK],
)
def test_broadcast_buttons_are_handled(callback):
    """Иначе они молча улетят в legacy-заглушку «кнопка устарела».

    Фильтры прогоняем по-настоящему, а не сверяем строки: так проверка
    переживёт смену способа их объявления.
    """
    from types import SimpleNamespace

    from bot.handlers.broadcast import router

    event = SimpleNamespace(data=callback)
    matched = any(
        all(flt.callback(event) for flt in (handler.filters or []))
        for handler in router.callback_query.handlers
    )
    assert matched, f"{callback} никто не обрабатывает"


def test_only_the_asked_button_appears():
    only_menu = broadcast_module.keyboard_for(make_broadcast(with_menu_button=True))

    assert len(only_menu.inline_keyboard) == 1
    assert only_menu.inline_keyboard[0][0].text == broadcast_module.MENU_BUTTON_TEXT


def test_no_button_when_not_asked():
    assert broadcast_module.keyboard_for(make_broadcast()) is None


def test_status_reflects_the_outcome():
    NexUserFactory()
    item = make_broadcast()

    run(FakeBot(), item)

    item.refresh_from_db()
    assert item.status == BroadcastStatusEnum.SENT
    assert item.finished_at is not None


def test_everyone_blocked_counts_as_failure():
    user = NexUserFactory()
    item = make_broadcast()

    run(FakeBot(fail_for=[user.pk]), item)

    item.refresh_from_db()
    assert item.status == BroadcastStatusEnum.FAILED


# --- рассылка, составленная в боте ---


class CopyingBot(FakeBot):
    """Бот, умеющий и копировать. Копии считаем отдельно от обычных отправок."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.copied: list[tuple[int, int, int]] = []

    async def copy_message(self, chat_id, from_chat_id, message_id, reply_markup=None):
        self.attempts[chat_id] = self.attempts.get(chat_id, 0) + 1
        if chat_id in self.fail_for:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        if chat_id in self.retry_once_for and self.attempts[chat_id] == 1:
            raise TelegramRetryAfter(method=None, message="Too Many Requests", retry_after=0)
        self.copied.append((chat_id, from_chat_id, message_id))


def test_broadcast_with_attachment_is_copied_not_retyped():
    """Сообщение с вложением уходит копией: пересобрать его текстом нельзя."""
    user = NexUserFactory()
    broadcast = make_broadcast(source_chat_id=777, source_message_id=42)
    bot = CopyingBot()

    async_to_sync(broadcast_module.run)(bot, broadcast.pk)

    assert bot.copied == [(user.pk, 777, 42)]
    assert bot.sent == []  # обычной отправки быть не должно


def test_plain_broadcast_still_goes_as_text():
    """Рассылка из админки не должна пострадать от появления копий."""
    user = NexUserFactory()
    broadcast = make_broadcast()
    bot = CopyingBot()

    async_to_sync(broadcast_module.run)(bot, broadcast.pk)

    assert bot.sent == [user.pk]
    assert bot.copied == []


def test_copied_broadcast_survives_blocked_user():
    """Блокировка одного не должна останавливать копирование остальным."""
    blocked = NexUserFactory()
    fine = NexUserFactory()
    broadcast = make_broadcast(source_chat_id=1, source_message_id=2)
    bot = CopyingBot(fail_for=[blocked.pk])

    result = async_to_sync(broadcast_module.run)(bot, broadcast.pk)

    assert [chat for chat, _, _ in bot.copied] == [fine.pk]
    assert result == {"sent": 1, "failed": 1}
    delivery = BroadcastDelivery.objects.get(broadcast=broadcast, user=blocked)
    assert delivery.is_delivered is False
    assert "заблокирован" in delivery.error


def test_repeat_sends_only_to_those_who_missed_it():
    """Повтор копии докидывает недошедшим, а дошедших не беспокоит второй раз."""
    blocked = NexUserFactory()
    fine = NexUserFactory()
    broadcast = make_broadcast(source_chat_id=1, source_message_id=2)

    async_to_sync(broadcast_module.run)(CopyingBot(fail_for=[blocked.pk]), broadcast.pk)

    second = CopyingBot()
    async_to_sync(broadcast_module.run)(second, broadcast.pk)

    assert [chat for chat, _, _ in second.copied] == [blocked.pk]
    assert fine.pk not in [chat for chat, _, _ in second.copied]
