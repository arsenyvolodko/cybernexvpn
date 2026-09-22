"""Как дашборд показывает состояние подписки.

Состояний три, а не два. Пока пробный период выдавался при /start, «подписки
нет вовсе» почти не встречалось — и страница знала только «активна» и
«истекла». После переноса пробного на кнопку «Подключиться» новичок, который
открыл бота и остановился, стал обычным делом, и такой человек показывался
как «подписка истекла», хотя он её никогда не заводил.
"""

import datetime as dt

import pytest
from django.utils.timezone import now

from nexvpn.api.tests.factories import NexUserFactory, SubscriptionFactory
from nexvpn.models import NexUser

pytestmark = pytest.mark.django_db


# --- три состояния подписки, а не два ---


def test_a_user_without_a_subscription_is_not_called_expired():
    """С тех пор как пробный стартует по «Подключиться», а не по /start,
    «подписки нет вовсе» — обычное состояние новичка. Раньше дашборд
    показывал такому человеку «подписка истекла»."""
    from nexvpn.dashboard.queries import _user_row

    newcomer = NexUserFactory()

    row = _user_row(newcomer)

    assert row["has_subscription"] is False
    assert row["is_active"] is False
    assert row["expires_at"] is None


def test_an_expired_subscription_still_reads_as_expired():
    from nexvpn.dashboard.queries import _user_row

    subscription = SubscriptionFactory(expires_at=now() - dt.timedelta(days=1))

    row = _user_row(NexUser.objects.select_related("subscription").get(pk=subscription.user_id))

    assert row["has_subscription"] is True
    assert row["is_active"] is False


def test_a_live_subscription_reads_as_active():
    from nexvpn.dashboard.queries import _user_row

    subscription = SubscriptionFactory(expires_at=now() + dt.timedelta(days=5))

    row = _user_row(NexUser.objects.select_related("subscription").get(pk=subscription.user_id))

    assert row["has_subscription"] is True
    assert row["is_active"] is True


# --- скидки в дашборде ---


def _discount(title, plans_prices, **kwargs):
    from datetime import timedelta

    from django.utils.timezone import now

    from nexvpn.models import Discount, DiscountPrice

    discount = Discount.objects.create(
        title=title, kind=kwargs.pop("kind", "code"), code=kwargs.pop("code", title.upper()[:10].replace(" ", "")),
        valid_until=now() + timedelta(days=30), **kwargs,
    )
    for plan, price in plans_prices.items():
        DiscountPrice.objects.create(discount=discount, plan=plan, price_month=price)
    return discount


def test_users_can_be_filtered_by_discount_and_rows_show_it():
    from nexvpn.api.tests.factories import PlanFactory
    from nexvpn.dashboard import periods, queries
    from nexvpn.subscription import discounts

    plan = PlanFactory(device_limit=1, price_month=150)
    spring = _discount("Весна", {plan: 99}, code="SPRING")
    summer = _discount("Лето", {plan: 75}, code="SUMMER")
    a, b, c, d = NexUserFactory(), NexUserFactory(), NexUserFactory(), NexUserFactory()
    discounts.activate(a, spring)
    discounts.activate(b, summer)
    discounts.activate(c, spring)
    discounts.activate(c, summer)  # весна заменена летом
    student = _discount("Студенты", {plan: 105}, kind="verification", code="")
    discounts.open_request(d, student)

    period = periods.parse({})
    ids = lambda rows: {r["id"] for r in rows}
    assert ids(queries.users("all", period, discount=str(spring.pk))) == {a.id}
    assert ids(queries.users("all", period, discount=str(summer.pk))) == {b.id, c.id}
    assert {a.id, b.id, c.id} <= ids(queries.users("all", period, discount="any"))
    assert d.id in ids(queries.users("all", period, discount="none"))
    assert a.id not in ids(queries.users("all", period, discount="none"))
    assert ids(queries.users("all", period, discount="pending")) == {d.id}

    row = next(r for r in queries.users("all", period) if r["id"] == c.id)
    assert row["discount"]["title"] == "Лето" and row["discount"]["via"] == "Сам: по коду"

    options = {o["title"]: o["active"] for o in queries.discount_options()}
    assert options["Весна"] == 1 and options["Лето"] == 2 and options["Студенты"] == 0


def test_user_card_shows_current_discount_and_history():
    from nexvpn.api.tests.factories import PlanFactory
    from nexvpn.dashboard import queries
    from nexvpn.subscription import discounts

    plan = PlanFactory(device_limit=1, price_month=150)
    user = NexUserFactory()
    discounts.activate(user, _discount("Весна", {plan: 99}, code="SPRING"))
    discounts.activate(user, _discount("Лето", {plan: 75}, code="SUMMER"))

    card = queries.user_card(user.id)
    assert card["discount"]["title"] == "Лето"
    assert [(h["title"], h["status"]) for h in card["discount_history"]] == [
        ("Лето", "Действует"), ("Весна", "Заменена другой"),
    ]


def test_dashboard_users_api_accepts_discount_filter(admin_client):
    response = admin_client.get("/dash/api/users/", {"segment": "all", "discount": "any"})
    assert response.status_code == 200
    assert "discounts" in response.json()


def test_admin_user_page_and_discount_filter(admin_client):
    from nexvpn.api.tests.factories import PlanFactory
    from nexvpn.subscription import discounts

    plan = PlanFactory(device_limit=1, price_month=150)
    user = NexUserFactory(username="withdisc")
    discount = _discount("Весна", {plan: 99}, code="SPRING")
    discounts.activate(user, discount)

    page = admin_client.get(f"/admin/nexvpn/nexuser/{user.id}/change/").content.decode()
    assert "Скидка сейчас" in page and "Весна (сам: по коду)" in page
    assert "Скидки и промокоды" in page

    listing = admin_client.get("/admin/nexvpn/nexuser/", {"discount": str(discount.pk)}).content.decode()
    assert "withdisc" in listing
    other = admin_client.get("/admin/nexvpn/nexuser/", {"discount": "none"}).content.decode()
    assert "withdisc" not in other


def test_active_week_needs_recent_online():
    from nexvpn.dashboard import queries
    from nexvpn.models import PanelPresence

    fresh = SubscriptionFactory(expires_at=now() + dt.timedelta(days=10)).user
    stale = SubscriptionFactory(expires_at=now() + dt.timedelta(days=10)).user
    PanelPresence.objects.create(user=fresh, first_connected_at=now() - dt.timedelta(days=30),
                                 online_at=now() - dt.timedelta(days=2))
    PanelPresence.objects.create(user=stale, first_connected_at=now() - dt.timedelta(days=30),
                                 online_at=now() - dt.timedelta(days=20))

    week = set(queries.segment_queryset("active_week").values_list("id", flat=True))
    active = set(queries.segment_queryset("active").values_list("id", flat=True))
    assert fresh.id in week and stale.id not in week
    assert {fresh.id, stale.id} <= active, "обычные «Активные» считают обоих"
    assert list(queries.SEGMENTS)[:2] == ["active", "active_week"], "рядом с «Активными»"


# --- скидку назначает администратор ---


def test_admin_assigns_and_revokes_discount_in_user_card(admin_client):
    from nexvpn.api.tests.factories import PlanFactory
    from nexvpn.models import UserDiscount
    from nexvpn.subscription import discounts

    plan = PlanFactory(device_limit=1, price_month=150)
    user = NexUserFactory(username="assignee")
    discount = _discount("Весна", {plan: 99}, code="SPRING")
    url = f"/admin/nexvpn/nexuser/{user.id}/change/"

    form = admin_client.get(url).context["adminform"].form
    data = {k: v for k, v in form.initial.items() if v is not None}
    for prefix in ("discounts", "subscription", "presence", "subscriptionevent_set", "payment_set",
                   "transaction_set", "sent_invitations"):
        data.update({f"{prefix}-TOTAL_FORMS": "0", f"{prefix}-INITIAL_FORMS": "0"})
    context = admin_client.get(url).context
    for formset in context["inline_admin_formsets"]:
        management = formset.formset.management_form
        data.update({management.add_prefix(k): v for k, v in management.initial.items()})
    data = {k: ("" if v is None else v) for k, v in data.items()}

    response = admin_client.post(url, {**data, "assign_discount": discount.pk})
    assert response.status_code == 302, response.content.decode()[:600]
    held = discounts.active_discount(user)
    assert held.discount == discount and held.via == UserDiscount.Via.ADMIN

    admin_client.post(url, {**data, "revoke_discount": "on"})
    assert discounts.active_discount(user) is None
    assert UserDiscount.objects.get(user=user).status == UserDiscount.Status.REVOKED


def test_bulk_assign_action(admin_client):
    from nexvpn.api.tests.factories import PlanFactory
    from nexvpn.models import UserDiscount
    from nexvpn.subscription import discounts

    plan = PlanFactory(device_limit=1, price_month=150)
    a, b = NexUserFactory(), NexUserFactory()
    discount = _discount("Весна", {plan: 99}, code="SPRING")
    base = {"action": "action_assign_discount", "_selected_action": [a.id, b.id]}

    page = admin_client.post("/admin/nexvpn/nexuser/", base)
    assert page.status_code == 200 and "Назначить скидку" in page.content.decode()

    done = admin_client.post("/admin/nexvpn/nexuser/", {**base, "apply": "1", "discount": discount.pk})
    assert done.status_code == 302
    for user in (a, b):
        assert discounts.active_discount(user).via == UserDiscount.Via.ADMIN


def test_admin_assigned_discount_has_plain_footer():
    from nexvpn.api.tests.factories import PlanFactory
    from bot.services import PriceView
    from nexvpn.models import UserDiscount
    from nexvpn.subscription import discounts

    plan = PlanFactory(device_limit=1, price_month=150)
    user = NexUserFactory()
    discounts.activate(user, _discount("Весна", {plan: 99}, code="SPRING"), via=UserDiscount.Via.ADMIN)
    footer = PriceView(book=discounts.book_for(user)).footer(plan)
    assert "«Весна»" in footer and "промокоду" not in footer
