"""Добор платежей опросом ЮKassa.

Написано после боевого случая: хостера приложения ЮKassa не пускает, вебхуки не
доходили вовсе, и человек заплатил 600₽ без единого начисленного дня. Оплата
без выдачи — худшее, что может случиться в платном сервисе, поэтому проверяем
не только счастливый путь, но и то, что добор не начислит дважды.
"""

from datetime import timedelta

import pytest
from django.utils.timezone import now

from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory
from nexvpn.enums import PaymentKindEnum
from nexvpn.models import Payment
from nexvpn.subscription import reconcile

pytestmark = pytest.mark.django_db


class FakeResponse:
    def __init__(self, status="succeeded", ok=True):
        self.ok = ok
        self.status_code = 200 if ok else 500
        self._status = status

    def json(self):
        # captured_at — момент, когда ЮKassa зафиксировала оплату.
        return {"status": self._status, "captured_at": "2026-08-11T17:54:00.000Z"}


def make_payment(**kwargs):
    subscription = SubscriptionFactory(plan=PlanFactory(device_limit=3, price_month=400))
    defaults = {
        "uuid": kwargs.pop("uuid", "11111111-1111-1111-1111-111111111111"),
        "idempotence_key": "22222222-2222-2222-2222-222222222222",
        "user": subscription.user,
        "plan": subscription.plan,
        "amount": 400,
        "kind": PaymentKindEnum.PURCHASE,
        "period_months": 1,
    }
    defaults.update(kwargs)
    return Payment.objects.create(**defaults), subscription


def patch_yookassa(monkeypatch, status="succeeded", ok=True):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(status, ok)

    monkeypatch.setattr("nexvpn.subscription.reconcile.requests.get", fake_get)
    monkeypatch.setattr("nexvpn.subscription.panel_sync.sync_subscription", lambda *a, **kw: True)
    return calls


def test_paid_payment_is_credited(monkeypatch):
    payment, subscription = make_payment()
    before = subscription.expires_at
    patch_yookassa(monkeypatch)

    result = reconcile.reconcile()

    payment.refresh_from_db()
    subscription.refresh_from_db()
    assert result.applied == 1
    assert payment.processed_at is not None
    assert (subscription.expires_at - before).days >= 30


def test_pending_payment_is_left_alone(monkeypatch):
    payment, subscription = make_payment()
    before = subscription.expires_at
    patch_yookassa(monkeypatch, status="pending")

    result = reconcile.reconcile()

    payment.refresh_from_db()
    subscription.refresh_from_db()
    assert result.still_pending == 1 and result.applied == 0
    assert payment.processed_at is None
    assert subscription.expires_at == before


def test_already_processed_is_not_credited_twice(monkeypatch):
    """Вебхук мог успеть первым — второй раз начислять нельзя."""
    payment, subscription = make_payment(processed_at=now())
    before = subscription.expires_at
    calls = patch_yookassa(monkeypatch)

    result = reconcile.reconcile()

    subscription.refresh_from_db()
    assert result.applied == 0
    assert not calls, "обработанный платёж вообще не надо спрашивать"
    assert subscription.expires_at == before


def test_unreachable_yookassa_does_not_lose_the_payment(monkeypatch):
    """Не смогли спросить — платёж остаётся в очереди на следующий прогон."""
    payment, _ = make_payment()
    patch_yookassa(monkeypatch, ok=False)

    result = reconcile.reconcile()

    payment.refresh_from_db()
    assert result.failed == 1
    assert payment.processed_at is None


def test_old_payments_are_not_polled_forever(monkeypatch):
    """ЮKassa сама отменяет неоплаченное через сутки — вечно спрашивать незачем."""
    payment, _ = make_payment()
    Payment.objects.filter(pk=payment.pk).update(created_at=now() - timedelta(days=5))
    calls = patch_yookassa(monkeypatch)

    result = reconcile.reconcile()

    assert result.checked == 0 and not calls


def test_person_is_told_once_even_if_both_paths_run(monkeypatch):
    """Вебхук и сверка идут к одной цели — сообщить человек должен один раз."""
    payment, _ = make_payment()
    patch_yookassa(monkeypatch)
    sent = []
    monkeypatch.setattr("bot.notify.notify_payment_applied", lambda chat_id, text, screen=None: sent.append(chat_id))

    reconcile.reconcile()
    reconcile.reconcile()  # второй прогон: платёж уже закрыт

    assert len(sent) == 1


def test_message_says_what_was_bought(monkeypatch):
    payment, subscription = make_payment()
    patch_yookassa(monkeypatch)
    sent = []
    monkeypatch.setattr("bot.notify.notify_payment_applied", lambda chat_id, text, screen=None: sent.append(text))

    reconcile.reconcile()

    assert "Оплата прошла" in sent[0]
    assert "3 устройства" in sent[0]


def test_test_payment_grants_nothing_but_confirms(monkeypatch):
    """Проверочный платёж существует ради факта подтверждения, а не ради дней."""
    payment, subscription = make_payment(kind=PaymentKindEnum.TEST, amount=5)
    before = subscription.expires_at
    patch_yookassa(monkeypatch)
    sent = []
    monkeypatch.setattr("bot.notify.notify_payment_applied", lambda chat_id, text, screen=None: sent.append(text))

    reconcile.reconcile()

    subscription.refresh_from_db()
    assert subscription.expires_at == before, "проверочный платёж не должен продлевать подписку"
    assert "Проверочный платёж" in sent[0]
    assert "опрос" in sent[0], "надо видеть, каким путём пришло подтверждение"


def test_delay_is_measured_from_payment_not_from_link(monkeypatch):
    """Замер обязан считать доставку, а не то, как долго человек вводил карту.

    Первая версия считала от создания ссылки — и показала «82 с» там, где
    вебхук пришёл за секунду.
    """
    from datetime import timedelta

    payment, _ = make_payment(kind=PaymentKindEnum.TEST, amount=5)
    # Ссылку создали давно, оплатили только что.
    Payment.objects.filter(pk=payment.pk).update(created_at=now() - timedelta(minutes=30))
    patch_yookassa(monkeypatch)
    sent = []
    monkeypatch.setattr("bot.notify.notify_payment_applied", lambda chat_id, text, screen=None: sent.append(text))
    monkeypatch.setattr(
        "nexvpn.subscription.reconcile.parse_paid_at", lambda remote: now() - timedelta(seconds=3)
    )

    reconcile.reconcile()

    assert "3 с" in sent[0], sent[0]
    assert "30" not in sent[0], "в задержку не должно попадать время до оплаты"


def calls_of(monkeypatch):
    """Перехватываем обращения к Bot API, не выходя в сеть."""
    calls = []
    monkeypatch.setattr("bot.notify._api", lambda method, payload: (calls.append((method, payload)), True)[1])
    return calls


def test_existing_screen_is_edited_not_duplicated(monkeypatch):
    """Человек ушёл платить с экрана оплаты — туда же логично и вернуть ответ."""
    from bot.notify import notify_payment_applied

    calls = calls_of(monkeypatch)
    notify_payment_applied(chat_id=1, text="ок", screen_message_id=555)

    assert [m for m, _ in calls] == ["editMessageText"]
    assert calls[0][1]["message_id"] == 555


def test_new_message_when_the_screen_is_gone(monkeypatch):
    """Экран могли удалить — ответ всё равно должен дойти, и с той же кнопкой."""
    from bot import notify

    calls = []

    def fake_api(method, payload):
        calls.append((method, payload))
        return method != "editMessageText"  # правка не удалась

    monkeypatch.setattr(notify, "_api", fake_api)
    notify.notify_payment_applied(chat_id=1, text="ок", screen_message_id=555)

    assert [m for m, _ in calls] == ["editMessageText", "sendMessage"]
    assert calls[1][1]["reply_markup"] == calls[0][1]["reply_markup"], "кнопка одинаковая в обоих путях"


def test_button_leads_to_its_own_handler(monkeypatch):
    """Кнопка «Отлично» должна сниматься нашим обработчиком, а не заглушкой."""
    from bot import notify
    from bot.handlers.broadcast import router

    calls = calls_of(monkeypatch)
    notify.notify_payment_applied(chat_id=1, text="ок", screen_message_id=None)
    markup = calls[0][1]["reply_markup"]
    callback = markup["inline_keyboard"][0][0]["callback_data"]

    assert callback == notify.PAYMENT_OK_CALLBACK
    from types import SimpleNamespace

    event = SimpleNamespace(data=callback)
    assert any(
        all(flt.callback(event) for flt in (h.filters or []))
        for h in router.callback_query.handlers
    )
