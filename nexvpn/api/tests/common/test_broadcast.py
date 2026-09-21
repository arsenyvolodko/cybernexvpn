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
    [
        broadcast_module.CONNECT_CALLBACK,
        broadcast_module.MENU_CALLBACK,
        broadcast_module.MENU_KEEP_REFERRAL_CALLBACK,
    ],
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


def test_referral_buttons_carry_each_recipients_own_link():
    item = make_broadcast(with_referral_buttons=True, with_menu_button=True)

    first = broadcast_module.keyboard_for(item, 101)
    second = broadcast_module.keyboard_for(item, 202)

    def copied(keyboard):
        return next(b.copy_text.text for row in keyboard.inline_keyboard for b in row if b.copy_text)

    assert copied(first).endswith("?start=101")
    assert copied(second).endswith("?start=202")
    texts = [row[0].text for row in first.inline_keyboard]
    assert texts == ["Поделиться ссылкой", "Скопировать ссылку", broadcast_module.MENU_BUTTON_TEXT]
    assert first.inline_keyboard[-1][0].callback_data == broadcast_module.MENU_KEEP_REFERRAL_CALLBACK


def test_plain_menu_button_keeps_its_old_callback():
    """Новая «В меню» — только для рассылок с рефералкой, обычная не меняется."""
    keyboard = broadcast_module.keyboard_for(make_broadcast(with_menu_button=True))

    assert keyboard.inline_keyboard[0][0].callback_data == broadcast_module.MENU_CALLBACK


def test_referral_broadcast_sends_each_their_own_keyboard():
    class RecordingBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.markups = {}

        async def send_message(self, chat_id, text, reply_markup=None):
            self.markups[chat_id] = reply_markup
            await super().send_message(chat_id, text, reply_markup)

    one, two = NexUserFactory(), NexUserFactory()
    bot = RecordingBot()
    run(bot, make_broadcast(with_referral_buttons=True))

    for user in (one, two):
        link = bot.markups[user.pk].inline_keyboard[1][0].copy_text.text
        assert link.endswith(f"?start={user.pk}")


class FakeMessage:
    def __init__(self, keyboard):
        from types import SimpleNamespace

        self.chat = SimpleNamespace(id=1)
        self.reply_markup = keyboard
        self.edited_to = "не трогали"
        self.answered = []

    async def edit_reply_markup(self, reply_markup=None):
        self.edited_to = reply_markup

    async def answer(self, text, reply_markup=None):
        self.answered.append(text)


class FakeCall:
    def __init__(self, message):
        self.message = message

    async def answer(self, *args, **kwargs):
        pass


def test_menu_on_referral_broadcast_removes_only_itself():
    from bot import texts
    from bot.handlers.broadcast import handle_menu_keeping_referral

    keyboard = broadcast_module.keyboard_for(
        make_broadcast(with_referral_buttons=True, with_menu_button=True), 101
    )
    message = FakeMessage(keyboard)

    async_to_sync(handle_menu_keeping_referral)(FakeCall(message))

    left = [row[0].text for row in message.edited_to.inline_keyboard]
    assert left == ["Поделиться ссылкой", "Скопировать ссылку"]
    assert message.answered == [texts.MAIN_MENU]


def test_plain_menu_still_removes_the_whole_keyboard():
    from bot.handlers.broadcast import handle_menu_from_broadcast

    message = FakeMessage(broadcast_module.keyboard_for(
        make_broadcast(with_connect_button=True, with_menu_button=True)
    ))

    async_to_sync(handle_menu_from_broadcast)(FakeCall(message))

    assert message.edited_to is None


def test_emoji_placeholders_become_telegram_tags():
    text = "{emoji_5231449120635370684:💸}Дни{emoji_5267300544094948794} и {обычные} скобки"

    assert broadcast_module.render_text(text) == (
        '<tg-emoji emoji-id="5231449120635370684">💸</tg-emoji>Дни'
        '<tg-emoji emoji-id="5267300544094948794">⭐</tg-emoji> и {обычные} скобки'
    )


def test_text_broadcast_goes_out_with_emoji_rendered():
    class RecordingBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.texts = []

        async def send_message(self, chat_id, text, reply_markup=None):
            self.texts.append(text)
            await super().send_message(chat_id, text, reply_markup)

    NexUserFactory()
    bot = RecordingBot()
    run(bot, make_broadcast(text="Привет {emoji_5303310030940952439:💖}"))

    assert bot.texts == ['Привет <tg-emoji emoji-id="5303310030940952439">💖</tg-emoji>']


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


# --- опросы ---


class PollBot(FakeBot):
    """Отвечает на send_poll так, как Telegram: сообщением с id опроса."""

    def __init__(self):
        super().__init__()
        self.polls = []

    async def send_poll(self, chat_id, question, options, **kwargs):
        from types import SimpleNamespace

        self.polls.append({"chat_id": chat_id, "question": question, "options": options, **kwargs})
        self.sent.append(chat_id)
        return SimpleNamespace(poll=SimpleNamespace(id=f"poll-{chat_id}"))


def make_poll(**kwargs):
    return make_broadcast(
        text=kwargs.pop("text", "Какой туннель лучше? {emoji_5406756500108501710:🆓}"),
        poll_options=kwargs.pop("poll_options", "Авто\nРоссия\n\nГермания\n"),
        **kwargs,
    )


def test_poll_broadcast_goes_out_as_a_non_anonymous_poll():
    user = NexUserFactory()
    bot = PollBot()

    run(bot, make_poll(poll_allows_multiple=True))

    poll = bot.polls[0]
    assert poll["chat_id"] == user.pk
    assert poll["options"] == ["Авто", "Россия", "Германия"], "пустые строки не варианты"
    assert poll["is_anonymous"] is False, "иначе не узнаем, кто за что голосовал"
    assert poll["allows_multiple_answers"] is True
    assert poll["question"] == (
        'Какой туннель лучше? <tg-emoji emoji-id="5406756500108501710">🆓</tg-emoji>'
    )
    assert BroadcastDelivery.objects.get(user=user).poll_id == f"poll-{user.pk}"


def test_vote_is_recorded_changed_and_withdrawn():
    from nexvpn.models import BroadcastPollVote

    user = NexUserFactory()
    item = make_poll()
    run(PollBot(), item)
    record = async_to_sync(broadcast_module.record_poll_answer)

    assert record(f"poll-{user.pk}", [1]) is True
    assert BroadcastPollVote.objects.get(broadcast=item, user=user).option_ids == [1]

    record(f"poll-{user.pk}", [0, 2])
    assert BroadcastPollVote.objects.get(broadcast=item, user=user).option_ids == [0, 2]

    record(f"poll-{user.pk}", [])
    assert not BroadcastPollVote.objects.filter(broadcast=item).exists()


def test_vote_in_unknown_poll_is_ignored():
    assert async_to_sync(broadcast_module.record_poll_answer)("чужой", [0]) is False


def test_bot_subscribes_to_poll_answers():
    """Бот запрашивает у Telegram только те события, на которые есть обработчики.
    Без обработчика голоса просто не придут."""
    from bot.handlers.broadcast import router

    # Собирать весь диспетчер тут нельзя: роутеры прикрепляются к корню
    # навсегда, и соседний тест, который собирает его сам, упадёт.
    assert "poll_answer" in router.resolve_used_update_types()


@pytest.mark.parametrize(
    ("fields", "error_field"),
    [
        ({"poll_options": "Только один"}, "poll_options"),
        ({"poll_options": "\n".join(str(i) for i in range(11))}, "poll_options"),
        ({"poll_options": "Да\nДа"}, "poll_options"),
        ({"poll_options": "Да\n" + "x" * 101}, "poll_options"),
        ({"text": "<b>Жирный</b> вопрос"}, "text"),
        ({"text": "x" * 301}, "text"),
    ],
)
def test_bad_poll_is_rejected_before_sending(fields, error_field):
    from django.core.exceptions import ValidationError

    item = Broadcast(title="т", text=fields.pop("text", "Вопрос?"), poll_options=fields.pop("poll_options", "А\nБ"))
    with pytest.raises(ValidationError) as exc:
        item.clean()
    assert error_field in exc.value.message_dict


def test_good_poll_passes_validation():
    Broadcast(title="т", text="Вопрос {emoji_1:💖}?", poll_options="А\nБ").clean()


def test_admin_shows_who_voted_for_what():
    from django.contrib.admin.sites import site

    alice = NexUserFactory(username="alice")
    bob = NexUserFactory(username=None)
    item = make_poll()
    run(PollBot(), item)
    record = async_to_sync(broadcast_module.record_poll_answer)
    record(f"poll-{alice.pk}", [0])
    record(f"poll-{bob.pk}", [0, 2])

    html = site._registry[Broadcast].poll_results(item)

    assert "Проголосовало: <b>2</b> из 2" in html
    assert "@alice" in html and str(bob.pk) in html
    assert "Германия" in html
