"""Подталкивания новичку, который открыл бота и не добавил устройство.

Главное, что здесь проверяется, — отсечка по времени выката. На 11.09.2026 в
базе 139 человек с непросроченной подпиской, которые ни разу не подключались;
без отсечки первый же проход разослал бы им подталкивания задним числом.
"""

import datetime as dt

import pytest
from asgiref.sync import async_to_sync
from django.utils.timezone import now

from bot import texts
from bot.onboarding import NUDGES, due_nudges, has_device, send_due_nudges
from nexvpn.api.tests.factories import NexUserFactory, PlanFactory, SubscriptionFactory
from nexvpn.models import OnboardingNudge

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def cutoff(settings):
    """Отсечку держим относительно «сейчас»: иначе тест про шаг в сутки
    начинает упираться в календарь, а не в логику."""
    moment = (now() - dt.timedelta(days=7)).isoformat()
    settings.ONBOARDING_NUDGES_SINCE = moment
    return moment


class FakeBot:
    """Ловит отправленное вместо Telegram."""

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": reply_markup})


def newcomer(minutes_ago: int, **extra):
    """Новичок, открывший бота столько-то минут назад."""
    user = NexUserFactory(activated_at=now() - dt.timedelta(minutes=minutes_ago), **extra)
    return user


def run(bot=None):
    bot = bot or FakeBot()
    sent, failed = async_to_sync(send_due_nudges)(bot)
    return bot, sent, failed


# --- отсечка: ради неё всё и затевалось ---


def test_users_from_before_the_feature_are_never_touched(settings):
    """Заходил до выката, но недавно — единственный, кого ловит именно отсечка.

    Тех, кто заходил давно, отсекает окно: дальше суток с хвостиком человек
    и так выпадает из выборки. А вот открывший бота за два часа до выката в
    окно попадает, и без отсечки получил бы подталкивание задним числом.
    """
    settings.ONBOARDING_NUDGES_SINCE = (now() - dt.timedelta(hours=1)).isoformat()
    just_missed = NexUserFactory(activated_at=now() - dt.timedelta(hours=2))

    bot, sent, _ = run()

    assert sent == 0
    assert bot.sent == []
    assert not OnboardingNudge.objects.filter(user=just_missed).exists()


def test_someone_who_arrived_after_the_cutoff_is_nudged(settings):
    """Обратная сторона: сразу после выката новичок подталкивание получает."""
    settings.ONBOARDING_NUDGES_SINCE = (now() - dt.timedelta(hours=3)).isoformat()
    newbie = NexUserFactory(activated_at=now() - dt.timedelta(minutes=11))

    bot, sent, _ = run()

    assert sent == 1 and bot.sent[0]["chat_id"] == newbie.pk


def test_long_gone_users_are_out_of_the_window():
    """Заведённые недели назад не кандидаты и без всякой отсечки."""
    old_timer = NexUserFactory(activated_at=now() - dt.timedelta(days=30))

    bot, sent, _ = run()

    assert sent == 0
    assert not OnboardingNudge.objects.filter(user=old_timer).exists()


def test_empty_cutoff_disables_everything(settings):
    settings.ONBOARDING_NUDGES_SINCE = ""
    newcomer(15)

    assert due_nudges() == []


def test_unparsable_cutoff_disables_rather_than_crashes(settings):
    """Кривое значение не должно превратиться в рассылку всем подряд."""
    settings.ONBOARDING_NUDGES_SINCE = "вчера"
    newcomer(15)

    assert due_nudges() == []


# --- когда отправляем ---


def test_nothing_before_the_first_step():
    newcomer(5)

    bot, sent, _ = run()

    assert sent == 0 and bot.sent == []


def test_first_nudge_after_ten_minutes():
    user = newcomer(11)

    bot, sent, _ = run()

    assert sent == 1
    assert bot.sent[0]["chat_id"] == user.pk
    assert bot.sent[0]["text"] == texts.ONBOARDING_NUDGE_10M
    assert "@arseny_volodko" in bot.sent[0]["text"]


def test_the_button_leads_forward():
    newcomer(11)

    bot, _, _ = run()

    labels = [b.text for row in bot.sent[0]["keyboard"].inline_keyboard for b in row]
    assert labels == ["Подключиться ⚡"], "одна кнопка вперёд, «Назад» тут некуда"


def test_same_step_is_not_repeated():
    newcomer(11)
    run()

    bot, sent, _ = run()

    assert sent == 0 and bot.sent == []


def test_steps_follow_one_another():
    """10 минут → час → 3 часа → сутки, каждый со своим текстом."""
    user = newcomer(11)
    expected = [
        texts.ONBOARDING_NUDGE_10M,
        texts.ONBOARDING_NUDGE_1H,
        texts.ONBOARDING_NUDGE_3H,
        texts.ONBOARDING_NUDGE_1D,
    ]
    seen = []
    for minutes in (11, 61, 181, 24 * 60 + 1):
        user.activated_at = now() - dt.timedelta(minutes=minutes)
        user.save(update_fields=["activated_at"])
        bot, _, _ = run()
        seen.extend(message["text"] for message in bot.sent)

    assert seen == expected


def test_legacy_users_are_left_alone():
    """У легаси подписка уже оплачена — тексты про «опробовать» не к ним."""
    newcomer(11, is_legacy=True)

    bot, sent, _ = run()

    assert sent == 0 and bot.sent == []


# --- устройство появилось ---


def test_a_connected_device_stops_the_nudges(monkeypatch):
    user = newcomer(11)
    SubscriptionFactory(user=user, plan=PlanFactory(device_limit=1, price_month=150),
                        panel_user_id=555)
    monkeypatch.setattr("nexvpn.subscription.panel_sync.list_devices",
                        lambda *a, **kw: [{"hwid": "A"}])

    bot, sent, _ = run()

    assert sent == 0 and bot.sent == []
    closed = OnboardingNudge.objects.filter(user=user)
    assert closed.count() == len(NUDGES), "закрываем все шаги разом"
    assert not closed.filter(sent=True).exists(), "ни одно не считается отправленным"


def test_device_appearing_later_cancels_the_rest(monkeypatch):
    user = newcomer(11)
    run()  # первое ушло, пока устройств не было

    SubscriptionFactory(user=user, plan=PlanFactory(device_limit=1, price_month=150),
                        panel_user_id=555)
    monkeypatch.setattr("nexvpn.subscription.panel_sync.list_devices",
                        lambda *a, **kw: [{"hwid": "A"}])
    user.activated_at = now() - dt.timedelta(minutes=61)
    user.save(update_fields=["activated_at"])

    bot, sent, _ = run()

    assert sent == 0 and bot.sent == []
    assert OnboardingNudge.objects.filter(user=user, sent=True).count() == 1


def test_a_user_without_a_subscription_never_touches_the_panel(monkeypatch):
    """Дешёвый путь: подписки нет — устройств быть не может, сеть не нужна."""
    def explode(*args, **kwargs):
        raise AssertionError("панель трогать не должны")

    monkeypatch.setattr("nexvpn.subscription.panel_sync.list_devices", explode)
    user = newcomer(11)

    assert has_device(user) is False
    bot, sent, _ = run()
    assert sent == 1


def test_a_silent_panel_neither_sends_nor_closes(monkeypatch):
    """Промолчать и вернуться позже — но не закрыть шаг молча навсегда."""
    from nexvpn.remnawave import RemnawaveError

    user = newcomer(11)
    SubscriptionFactory(user=user, plan=PlanFactory(device_limit=1, price_month=150),
                        panel_user_id=555)
    monkeypatch.setattr("nexvpn.subscription.panel_sync.list_devices",
                        lambda *a, **kw: (_ for _ in ()).throw(RemnawaveError("нет связи")))

    bot, sent, _ = run()

    assert sent == 0 and bot.sent == []
    assert not OnboardingNudge.objects.filter(user=user).exists(), "шаг должен остаться открытым"


# --- простой задачи ---


def test_downtime_sends_only_the_latest_step():
    """Бот лежал сутки — человек получает одно сообщение, а не четыре."""
    user = newcomer(24 * 60 + 5)

    bot, sent, _ = run()

    assert sent == 1
    assert bot.sent[0]["text"] == texts.ONBOARDING_NUDGE_1D
    closed = OnboardingNudge.objects.filter(user=user)
    assert closed.count() == len(NUDGES)
    assert closed.filter(sent=True).count() == 1, "остальные закрыты без отправки"


def test_people_far_past_the_last_step_drop_out_of_sight():
    """Окно не бесконечное: через сутки с лишним человек больше не кандидат."""
    newcomer(24 * 60 + 5 * 60)

    assert due_nudges() == []
