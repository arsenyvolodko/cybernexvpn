"""Скидки и промокоды.

Главное, что проверяем: цена, которую человек видит, сумма, которую он платит,
и сумма, которую ждёт вебхук, считаются по одной и той же скидке. Разойдись
они — человек либо переплатит, либо заплатит, а тариф не сменится.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError
from django.utils.timezone import now

from nexvpn.api.tests.factories import NexUserFactory, PlanFactory, SubscriptionFactory
from nexvpn.models import BillingPeriod, Discount, DiscountPrice, Payment, UserDiscount
from nexvpn.subscription import discounts, service

pytestmark = pytest.mark.django_db


@pytest.fixture
def plans():
    BillingPeriod.objects.all().delete()
    BillingPeriod.objects.create(months=1, discount_percent=0)
    BillingPeriod.objects.create(months=12, discount_percent=20)
    return {limit: PlanFactory(device_limit=limit, price_month=price)
            for limit, price in [(1, 150), (3, 400), (5, 600)]}


def make_discount(prices, *, kind=Discount.Kind.CODE, code="SPRING", **kwargs):
    discount = Discount.objects.create(
        title=kwargs.pop("title", "Весна"),
        kind=kind,
        # У скидки с подтверждением код по желанию — только если передан явно.
        code=code if kind == Discount.Kind.CODE else kwargs.pop("verification_code", ""),
        valid_until=kwargs.pop("valid_until", now() + timedelta(days=30)),
        **kwargs,
    )
    for plan, price in prices.items():
        DiscountPrice.objects.create(discount=discount, plan=plan, price_month=price)
    return discount


# --- книга цен ---


def test_without_discount_prices_are_catalog(plans):
    book = discounts.book_for(NexUserFactory())
    assert book.price_month(plans[3]) == 400
    assert {p.device_limit for p in book.visible_plans()} >= {1, 3, 5}


def test_discount_shows_only_its_plans_at_its_prices(plans):
    user = NexUserFactory()
    discounts.activate(user, make_discount({plans[1]: 99, plans[3]: 280}))

    book = discounts.book_for(user)
    assert book.price_month(plans[1]) == 99 and book.price_month(plans[3]) == 280
    assert book.price_month(plans[5]) == 600, "не в скидке — каталог"
    assert sorted(p.device_limit for p in book.visible_plans()) == [1, 3]
    assert not book.can_choose(plans[5])


def test_expired_or_disabled_discount_stops_working(plans):
    user = NexUserFactory()
    discount = make_discount({plans[1]: 99})
    discounts.activate(user, discount)

    Discount.objects.filter(pk=discount.pk).update(valid_until=now() - timedelta(minutes=1))
    assert discounts.book_for(user).discount is None

    Discount.objects.filter(pk=discount.pk).update(valid_until=now() + timedelta(days=1), is_active=False)
    assert discounts.book_for(user).discount is None


# --- промокод ---


def test_code_is_case_insensitive_and_new_one_replaces_old(plans):
    user = NexUserFactory()
    first = make_discount({plans[1]: 99}, code="SPRING")
    second = make_discount({plans[3]: 300}, code="SUMMER", title="Лето")

    assert discounts.apply_code(user, "  spring ").discount == first
    assert discounts.apply_code(user, "summer").discount == second

    assert discounts.active_discount(user).discount == second
    assert UserDiscount.objects.get(user=user, discount=first).status == UserDiscount.Status.REPLACED


@pytest.mark.parametrize(
    ("case", "status"),
    [
        ("unknown", "not_found"),
        ("garbage", "not_found"),
        ("verification", "not_found"),
        ("expired", "unavailable"),
        ("disabled", "unavailable"),
        ("not_allowed", "unavailable"),
    ],
)
def test_code_that_does_not_apply(plans, case, status):
    user = NexUserFactory()
    other = NexUserFactory()
    kwargs = {
        "expired": {"valid_until": now() - timedelta(days=1)},
        "disabled": {"is_active": False},
        "not_allowed": {"is_public": False, "user_ids": str(other.pk)},
    }.get(case, {})
    kind = Discount.Kind.VERIFICATION if case == "verification" else Discount.Kind.CODE
    make_discount({plans[1]: 99}, kind=kind, code="SPRING", **kwargs)

    raw = {"unknown": "NOPE", "garbage": "spr ing!"}.get(case, "SPRING")
    result = discounts.apply_code(user, raw)
    assert result.discount is None and result.status == status
    assert discounts.active_discount(user) is None


def test_private_code_works_for_listed_user(plans):
    user = NexUserFactory()
    make_discount({plans[1]: 99}, is_public=False, user_ids=f"123, {user.pk}")
    assert discounts.apply_code(user, "SPRING").discount is not None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("506954303", ("506954303", "")),
        ("promo_SPRING", ("", "SPRING")),
        ("506954303_promo_SPRING", ("506954303", "SPRING")),
        ("", ("", "")),
    ],
)
def test_start_payload(payload, expected):
    assert discounts.parse_start_payload(payload) == expected


def test_link_uses_start_promo(plans, settings):
    settings.TG_BOT_URL = "https://t.me/CyberNexVpnBot"
    assert make_discount({plans[1]: 99}).link == "https://t.me/CyberNexVpnBot?start=promo_SPRING"


# --- что видит человек ---


def test_renew_and_plan_options_use_discount(plans):
    from bot.services import get_plan_options, get_renew_options

    sub = SubscriptionFactory(plan=plans[3], expires_at=now() + timedelta(days=30))
    discounts.activate(sub.user, make_discount({plans[1]: 99, plans[3]: 280}))

    _, renew = async_to_sync(get_renew_options)(sub.user)
    assert {o.months: o.price for o in renew} == {1: 280, 12: 280 * 12 * 80 // 100}

    _, options = async_to_sync(get_plan_options)(sub.user)
    shown = {o.device_limit: (o.price_month, o.is_discounted) for o in options}
    assert shown == {1: (99, True), 3: (280, True)}, "5 устройств скрыт"


def test_current_plan_stays_visible_even_if_not_in_discount(plans):
    from bot.services import get_plan_options

    sub = SubscriptionFactory(plan=plans[5])
    discounts.activate(sub.user, make_discount({plans[1]: 99}))

    _, options = async_to_sync(get_plan_options)(sub.user)
    assert {o.device_limit: o.is_current for o in options} == {1: False, 5: True}


def test_footer_names_the_discount_only_when_price_is_discounted(plans):
    from bot import texts
    from bot.services import PriceView

    view = PriceView(book=discounts.book_of(make_discount({plans[1]: 99}, title="Весна <25>")))
    assert view.footer(plans[1]) == texts.PRICE_FOOTER_CODE.format(title="Весна &lt;25&gt;")
    assert view.footer(plans[5]) == ""

    student = PriceView(book=discounts.book_of(
        make_discount({plans[1]: 99}, kind=Discount.Kind.VERIFICATION, title="Скидка для студентов 30%")
    ))
    assert student.footer(plans[1]).endswith("<b>Цены отображаются с учетом скидки «Скидка для студентов 30%»</b>")


# --- деньги ---


@pytest.fixture
def fake_payment(monkeypatch):
    calls = []

    def create(user, plan, **kwargs):
        calls.append({"plan": plan, **kwargs})
        return SimpleNamespace(url="https://pay", payment=SimpleNamespace(pk=1))

    monkeypatch.setattr("bot.services.payments.create_payment", create)
    return calls


def test_renew_payment_charges_discounted_price_and_records_discount(plans, fake_payment):
    from bot.services import start_renew_payment

    sub = SubscriptionFactory(plan=plans[3])
    discount = make_discount({plans[3]: 280})
    discounts.activate(sub.user, discount)

    async_to_sync(start_renew_payment)(sub.user, 12, "https://t.me/bot")

    assert fake_payment[0]["amount"] == 280 * 12 * 80 // 100
    assert fake_payment[0]["discount"] == discount


def test_renew_of_plan_outside_discount_is_catalog(plans, fake_payment):
    from bot.services import start_renew_payment

    sub = SubscriptionFactory(plan=plans[5])
    discounts.activate(sub.user, make_discount({plans[1]: 99}))

    async_to_sync(start_renew_payment)(sub.user, 1, "https://t.me/bot")

    assert fake_payment[0]["amount"] == 600
    assert fake_payment[0]["discount"] is None


def test_hidden_plan_cannot_be_bought_or_switched_to(plans, fake_payment):
    from bot.services import change_plan_free, start_plan_change_payment

    sub = SubscriptionFactory(plan=plans[1], expires_at=now() + timedelta(days=3))
    discounts.activate(sub.user, make_discount({plans[1]: 99, plans[3]: 280}))

    with pytest.raises(service.SubscriptionError):
        async_to_sync(start_plan_change_payment)(sub.user, 5, "https://t.me/bot")
    with pytest.raises(service.SubscriptionError):
        async_to_sync(change_plan_free)(sub.user, 5)
    assert fake_payment == []


def test_webhook_accepts_topup_priced_by_the_payments_discount(plans):
    """Счёт выставлен по скидке, а к оплате она уже истекла — тариф всё равно меняется."""
    sub = SubscriptionFactory(plan=plans[1], expires_at=now() + timedelta(days=3))
    discount = make_discount({plans[1]: 99, plans[3]: 280})
    discounts.activate(sub.user, discount)
    quote = service.quote_plan_change(sub.user, plans[3])
    amount = quote.topup_price
    assert amount < 400, "по скидке дешевле каталога"

    Discount.objects.filter(pk=discount.pk).update(valid_until=now() - timedelta(minutes=1))
    payment = Payment.objects.create(
        uuid="00000000-0000-0000-0000-000000000001", idempotence_key="00000000-0000-0000-0000-000000000002",
        user=sub.user, plan=plans[3], amount=amount, discount=discount,
    )

    service.change_plan_now(sub.user, plans[3], amount_paid=amount, payment=payment)

    sub.refresh_from_db()
    assert sub.plan == plans[3]


def test_webhook_still_rejects_real_underpayment(plans):
    sub = SubscriptionFactory(plan=plans[1], expires_at=now() + timedelta(days=3))
    payment = Payment.objects.create(
        uuid="00000000-0000-0000-0000-000000000003", idempotence_key="00000000-0000-0000-0000-000000000004",
        user=sub.user, plan=plans[3], amount=100,
    )
    with pytest.raises(service.SubscriptionError):
        service.change_plan_now(sub.user, plans[3], amount_paid=100, payment=payment)


# --- скидка с подтверждением ---


def test_verification_request_is_single_and_decided_once(plans):
    user = NexUserFactory()
    old = make_discount({plans[3]: 300}, code="OLD", title="Старая")
    discounts.activate(user, old)
    student = make_discount({plans[1]: 99}, kind=Discount.Kind.VERIFICATION, title="Студенты", show_in_menu=True,
                            verification_prompt="Пришли студак")

    request, created = discounts.open_request(user, student)
    again, created_again = discounts.open_request(user, student)
    assert created and not created_again and again.pk == request.pk
    assert discounts.request_state(user, student) == "pending"

    decided = discounts.decide(request.pk, approve=True)
    assert decided.status == UserDiscount.Status.ACTIVE
    assert discounts.active_discount(user).discount == student
    assert UserDiscount.objects.get(user=user, discount=old).status == UserDiscount.Status.REPLACED
    assert discounts.decide(request.pk, approve=False) is None, "второй раз не решаем"


def test_rejected_request_gives_nothing(plans):
    user = NexUserFactory()
    student = make_discount({plans[1]: 99}, kind=Discount.Kind.VERIFICATION, title="Студенты")
    request, _ = discounts.open_request(user, student)

    assert discounts.decide(request.pk, approve=False).status == UserDiscount.Status.REJECTED
    assert discounts.active_discount(user) is None


def test_student_discount_is_seeded_for_the_menu():
    seeded = Discount.objects.get(title="Скидка для студентов 30%")
    assert seeded.kind == Discount.Kind.VERIFICATION and seeded.show_in_menu
    assert "студент" in seeded.verification_prompt


# --- админка ---


@pytest.mark.parametrize(
    ("fields", "error_field"),
    [
        ({"kind": "code", "code": "a b"}, "code"),
        ({"kind": "code", "code": "ab"}, "code"),
        ({"kind": "code", "code": "SPRING", "show_in_menu": True}, "show_in_menu"),
        ({"kind": "verification", "code": "a b"}, "code"),
        ({"kind": "verification", "show_in_menu": True, "verification_prompt": ""}, "verification_prompt"),
        ({"kind": "code", "code": "SPRING", "is_public": False, "user_ids": ""}, "user_ids"),
        ({"kind": "code", "code": "SPRING", "is_public": True, "user_ids": "123"}, "user_ids"),
    ],
)
def test_bad_discount_is_rejected(fields, error_field):
    item = Discount(title="т", valid_until=now() + timedelta(days=1), **fields)
    with pytest.raises(ValidationError) as exc:
        item.clean()
    assert error_field in exc.value.message_dict


def test_duplicate_code_is_rejected_regardless_of_case(plans):
    make_discount({plans[1]: 99}, code="SPRING")
    with pytest.raises(ValidationError):
        Discount(title="т", kind="code", code="spring", valid_until=now() + timedelta(days=1)).clean()


def test_admin_requires_at_least_one_price(admin_client, plans):
    base = {
        "title": "Весна", "kind": "code", "code": "SPRING", "valid_until_0": "2030-01-01", "valid_until_1": "00:00:00",
        "is_active": "on", "is_public": "on", "user_ids": "", "verification_prompt": "",
        "prices-TOTAL_FORMS": "0", "prices-INITIAL_FORMS": "0", "prices-MIN_NUM_FORMS": "1", "prices-MAX_NUM_FORMS": "1000",
    }
    response = admin_client.post("/admin/nexvpn/discount/add/", base)
    assert response.status_code == 200 and not Discount.objects.filter(code="SPRING").exists()

    ok = admin_client.post("/admin/nexvpn/discount/add/", {
        **base, "prices-TOTAL_FORMS": "1", "prices-0-plan": plans[1].pk, "prices-0-price_month": "99",
    })
    assert ok.status_code == 302, ok.content.decode()[:800]
    assert Discount.objects.get(code="SPRING").prices.get().price_month == 99


# --- сценарии бота ---


class FakeState:
    def __init__(self):
        self.state, self.data = None, {}

    async def set_state(self, state):
        self.state = state

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def clear(self):
        self.state, self.data = None, {}


class FakeMessage:
    def __init__(self, user, text="", album=None):
        self.from_user = SimpleNamespace(id=user.pk, username="vasya", full_name="Вася")
        self.text, self.caption = text, None
        self.media_group_id = album
        self.answers, self.to_admin, self.forwarded_to = [], [], []
        outer = self

        class Bot:
            async def send_message(self, chat_id, text, reply_markup=None):
                outer.to_admin.append((chat_id, text, reply_markup))

        self.bot = Bot()

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def forward(self, chat_id):
        self.forwarded_to.append(chat_id)


def test_entering_code_applies_it_or_lets_retry(plans):
    from bot import texts
    from bot.handlers.promo import handle_promo_code

    user = NexUserFactory()
    make_discount({plans[1]: 99}, title="Весна")
    state = FakeState()
    state.state = "waiting"

    wrong = FakeMessage(user, "NOPE")
    async_to_sync(handle_promo_code)(wrong, user, state)
    assert wrong.answers[0][0] == texts.PROMO_NOT_FOUND and state.state == "waiting"

    make_discount({plans[1]: 99}, code="OLD", title="Старый", valid_until=now() - timedelta(days=1))
    stale = FakeMessage(user, "old")
    async_to_sync(handle_promo_code)(stale, user, state)
    assert stale.answers[0][0] == "К сожалению, данный промокод больше не доступен для применения."

    right = FakeMessage(user, "spring")
    async_to_sync(handle_promo_code)(right, user, state)
    assert right.answers[0][0] == "Промокод «Весна» применён, цены на тарифы обновлены."
    assert state.state is None


def _student(plans):
    return make_discount({plans[1]: 99}, kind=Discount.Kind.VERIFICATION, title="Студенты",
                         show_in_menu=True, verification_prompt="Пришли студак")


def test_single_proof_reaches_admin_and_nothing_after_it(plans, settings):
    from aiogram.dispatcher.event.bases import SkipHandler

    from bot import texts
    from bot.handlers.promo import handle_discount_proof

    settings.TG_ADMIN_USER_ID = 999
    user = NexUserFactory()
    student = _student(plans)
    state = FakeState()
    state.data = {"discount_id": student.pk}

    first = FakeMessage(user, "вот студак")
    async_to_sync(handle_discount_proof)(first, user, state)

    header = first.to_admin[0]
    assert header[0] == 999 and f"id: <code>{user.pk}</code>" in header[1] and "Студенты" in header[1]
    assert [b.text for b in header[2].inline_keyboard[0]] == ["Подтвердить", "❌ Отклонить"]
    assert first.forwarded_to == [999]
    assert first.answers[0][0] == texts.DISCOUNT_REQUEST_RECEIVED
    assert "добавить" not in texts.DISCOUNT_REQUEST_RECEIVED

    after = FakeMessage(user, "а ещё вопрос")
    with pytest.raises(SkipHandler):
        async_to_sync(handle_discount_proof)(after, user, state)
    assert after.forwarded_to == [] and after.answers == [] and state.state is None
    assert UserDiscount.objects.filter(user=user, discount=student).count() == 1


def test_album_counts_as_one_send(plans, settings):
    from aiogram.dispatcher.event.bases import SkipHandler

    from bot.handlers.promo import handle_discount_proof

    settings.TG_ADMIN_USER_ID = 999
    user = NexUserFactory()
    student = _student(plans)
    state = FakeState()
    state.data = {"discount_id": student.pk}

    photos = [FakeMessage(user, album="A1") for _ in range(3)]
    for photo in photos:
        async_to_sync(handle_discount_proof)(photo, user, state)

    assert [p.forwarded_to for p in photos] == [[999], [999], [999]]
    assert len(photos[0].to_admin) == 1 and photos[1].to_admin == photos[2].to_admin == []
    assert len(photos[0].answers) == 1 and photos[1].answers == photos[2].answers == []

    other = FakeMessage(user, album="B2")
    with pytest.raises(SkipHandler):
        async_to_sync(handle_discount_proof)(other, user, state)
    assert other.forwarded_to == []


def test_admin_approval_notifies_user_and_marks_request(plans):
    from bot.handlers.promo import handle_discount_review
    from bot.keyboards.factories import DiscountReviewCallback

    user = NexUserFactory()
    student = make_discount({plans[1]: 99}, kind=Discount.Kind.VERIFICATION, title="Скидка для студентов 30%")
    request, _ = discounts.open_request(user, student)
    sent, edited, alerts = [], [], []

    class Message:
        html_text = "🎓 <b>Заявка</b>"

        async def edit_text(self, text, reply_markup=None):
            edited.append(text)

    class Bot:
        async def send_message(self, chat_id, text, reply_markup=None):
            sent.append((chat_id, text))

    class Call:
        message, bot = Message(), Bot()

        async def answer(self, text=None, show_alert=False):
            if show_alert:
                alerts.append(text)

    async_to_sync(handle_discount_review)(Call(), DiscountReviewCallback(request_id=request.pk, approve=True))
    assert sent == [(user.pk, "Скидка для студентов 30% успешно применена!\nЦены на тарифы обновлены.")]
    assert edited[0].endswith("✅ Подтверждено")
    assert discounts.active_discount(user).discount == student

    async_to_sync(handle_discount_review)(Call(), DiscountReviewCallback(request_id=request.pk, approve=False))
    assert alerts == ["Эту заявку уже разобрали."]


def test_promo_link_is_announced_before_the_channel_gate(plans, monkeypatch):
    from bot import texts
    from bot.handlers import menu

    user = NexUserFactory()
    make_discount({plans[1]: 99}, title="Весна")
    monkeypatch.setattr(menu, "gate_required_for", lambda u: True)
    message = FakeMessage(user)

    async_to_sync(menu.handle_start)(message, SimpleNamespace(args="promo_SPRING"), user, True)

    assert message.answers[0][0] == "Промокод «Весна» успешно применён!"
    assert message.answers[1][0] == texts.CHANNEL_GATE
    assert discounts.active_discount(user) is not None


def test_expired_promo_link_says_no_longer_available(plans, monkeypatch):
    from bot.handlers import menu

    user = NexUserFactory()
    make_discount({plans[1]: 99}, valid_until=now() - timedelta(days=1))
    monkeypatch.setattr(menu, "gate_required_for", lambda u: False)
    message = FakeMessage(user)

    async_to_sync(menu.handle_start)(message, SimpleNamespace(args="promo_SPRING"), user, True)

    assert message.answers[0][0] == "К сожалению, данный промокод больше не доступен для применения."


def test_resubmitting_while_pending_replaces_old_request(plans, settings):
    """Раньше при висящей заявке кнопка отвечала «уже на проверке» и не
    включала приём — присланное следом фото получало в ответ просто меню."""
    from bot import texts
    from bot.handlers.promo import handle_discount_button, handle_discount_proof
    from bot.keyboards.factories import DiscountCallback

    settings.TG_ADMIN_USER_ID = 999
    user = NexUserFactory()
    student = _student(plans)
    old, _ = discounts.open_request(user, student)
    rendered = []

    class Call:
        message = None

        async def answer(self, *args, **kwargs):
            pass

    import bot.handlers.promo as promo

    async def fake_render(event, text, keyboard=None, **kwargs):
        rendered.append(text)

    state = FakeState()
    original = promo.render
    promo.render = fake_render
    try:
        async_to_sync(handle_discount_button)(Call(), DiscountCallback(discount_id=student.pk), user, state)
    finally:
        promo.render = original
    assert state.state is not None, "приём подтверждения включён"
    assert rendered[0].endswith(texts.DISCOUNT_RESUBMIT_NOTE)

    photos = [FakeMessage(user, album="A1") for _ in range(2)]
    for photo in photos:
        async_to_sync(handle_discount_proof)(photo, user, state)

    old.refresh_from_db()
    assert old.status == UserDiscount.Status.REPLACED
    fresh = UserDiscount.objects.get(user=user, discount=student, status=UserDiscount.Status.PENDING)
    assert fresh.pk != old.pk, "альбом не заменил заявку, которую сам же открыл"
    assert len(photos[0].to_admin) == 1 and photos[0].answers[0][0] == texts.DISCOUNT_REQUEST_RECEIVED
    assert discounts.decide(old.pk, approve=True) is None, "по старой заявке решить уже нельзя"


def test_rejection_does_not_block_a_new_attempt(plans):
    user = NexUserFactory()
    student = _student(plans)
    first, _ = discounts.open_request(user, student)
    discounts.decide(first.pk, approve=False)

    assert discounts.request_state(user, student) == "none"
    second, created = discounts.open_request(user, student)
    assert created and second.pk != first.pk


# --- надпись «-N%✅» на кнопках с ценой ---


@pytest.mark.parametrize(("badge", "mark"), [("50", "-50%✅ "), ("-30%", "-30%✅ "), (" 15 ", "-15%✅ "), ("", "")])
def test_button_mark_from_badge(badge, mark):
    assert Discount(title="т", badge=badge).button_mark == mark


def test_price_buttons_show_mark_only_for_discounted_plans(plans):
    from bot.keyboards import keyboards
    from bot.services import get_plan_options, get_plan_topup_options, get_renew_options

    sub = SubscriptionFactory(plan=plans[1], expires_at=now() + timedelta(days=3))
    discounts.activate(sub.user, make_discount({plans[1]: 75, plans[3]: 200}, badge="50"))

    _, renew = async_to_sync(get_renew_options)(sub.user)
    labels = [row[0].text for row in keyboards.renew(renew).inline_keyboard[:-1]]
    assert labels == ["-50%✅ 1 мес. — 75₽", "-50%✅ 12 мес. — 720₽ (60₽/мес)"]

    _, options = async_to_sync(get_plan_options)(sub.user)
    plan_labels = [row[0].text for row in keyboards.plan_list(options).inline_keyboard[:-1]]
    assert plan_labels == [f"-50%✅ {plans[3].name} — от 160₽/мес"]

    topup = async_to_sync(get_plan_topup_options)(sub.user, 3)
    assert all(o.mark == "-50%✅ " for o in topup)


def test_no_mark_without_badge_or_outside_discount(plans):
    from bot.services import get_renew_options

    sub = SubscriptionFactory(plan=plans[5])
    discounts.activate(sub.user, make_discount({plans[1]: 75}, badge="50"))  # текущего тарифа в скидке нет

    _, renew = async_to_sync(get_renew_options)(sub.user)
    assert {o.mark for o in renew} == {""}


def test_cu_discount_is_seeded_and_student_has_badge():
    cu = Discount.objects.get(title="Скидка для студентов ЦУ -50%")
    assert cu.kind == Discount.Kind.VERIFICATION and cu.show_in_menu and cu.badge == "50"
    assert "ЦУ" in cu.verification_prompt
    assert Discount.objects.get(title="Скидка для студентов 30%").badge == "30"


# --- одна скидка: и кодом, и заявкой ---


def _both_ways(plans):
    return make_discount(
        {plans[1]: 75}, kind=Discount.Kind.VERIFICATION, title="Скидка ЦУ", badge="50",
        show_in_menu=True, verification_prompt="Пришли студак", verification_code="CU2026",
    )


def test_verification_discount_may_have_a_code(plans, settings):
    settings.TG_BOT_URL = "https://t.me/CyberNexVpnBot"
    discount = _both_ways(plans)
    discount.clean()
    assert discount.link == "https://t.me/CyberNexVpnBot?start=promo_CU2026"


def test_code_gives_verification_discount_at_once_with_promo_footer(plans):
    from bot.services import PriceView

    user = NexUserFactory()
    discount = _both_ways(plans)

    result = discounts.apply_code(user, "cu2026")

    assert result.discount == discount
    held = discounts.active_discount(user)
    assert held.via == UserDiscount.Via.CODE
    assert PriceView(book=discounts.book_for(user)).footer(plans[1]).endswith(
        "по промокоду «Скидка ЦУ»</b>"
    )
    assert discounts.request_state(user, discount) == "active", "кнопка скажет «уже действует»"


def test_same_discount_by_request_has_plain_footer(plans):
    from bot.services import PriceView

    user = NexUserFactory()
    discount = _both_ways(plans)
    request, _ = discounts.open_request(user, discount)
    discounts.decide(request.pk, approve=True)

    assert discounts.active_discount(user).via == UserDiscount.Via.REQUEST
    footer = PriceView(book=discounts.book_for(user)).footer(plans[1])
    assert footer.endswith("скидки «Скидка ЦУ»</b>") and "промокоду" not in footer


def test_code_is_unique_across_kinds(plans):
    _both_ways(plans)
    with pytest.raises(ValidationError):
        Discount(title="т", kind="code", code="cu2026", valid_until=now() + timedelta(days=1)).clean()


def test_code_only_discounts_are_seeded_and_hidden_from_menu():
    user = NexUserFactory()
    for title, code, badge in [("Скидка 30%", "NEX30X85N8", "30"), ("Скидка 50%", "NEX50B26SW", "50")]:
        discount = Discount.objects.get(code=code)
        assert discount.title == title and discount.kind == Discount.Kind.CODE and discount.badge == badge
        assert not discount.show_in_menu and discount not in discounts.menu_discounts(user)
