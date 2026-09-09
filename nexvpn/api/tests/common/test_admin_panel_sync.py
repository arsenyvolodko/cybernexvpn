"""Правка подписки в админке должна доезжать до панели.

До этого дата менялась только у нас: админка показывала один срок, а панель
пускала или не пускала человека по другому. Расхождение молчаливое — оно
проявляется отказом в доступе, а не ошибкой.
"""

import datetime as dt

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User

from nexvpn.admin import SubscriptionAdmin
from nexvpn.api.tests.factories import PlanFactory, SubscriptionFactory
from nexvpn.enums import PanelSyncStatusEnum
from nexvpn.models import Subscription
from nexvpn.remnawave import RemnawaveError

pytestmark = pytest.mark.django_db


class FakeRequest:
    def __init__(self):
        self.messages = []
        self.user = None
        self._messages = self


@pytest.fixture
def admin_view(monkeypatch):
    view = SubscriptionAdmin(Subscription, AdminSite())
    monkeypatch.setattr(view, "message_user", lambda request, text, level=None: request.messages.append(text))
    return view


@pytest.fixture
def subscription():
    return SubscriptionFactory(plan=PlanFactory(device_limit=3, price_month=400))


def save(view, subscription, request):
    view.save_model(request, subscription, form=None, change=True)


def test_saving_pushes_to_the_panel(admin_view, subscription, monkeypatch):
    pushed = []
    monkeypatch.setattr("nexvpn.admin.panel_sync.sync_subscription",
                        lambda obj: pushed.append(obj.pk) or True)
    request = FakeRequest()
    subscription.expires_at = subscription.expires_at + dt.timedelta(days=10)

    save(admin_view, subscription, request)

    assert pushed == [subscription.pk]
    assert "и в панели" in request.messages[0]


def test_new_date_is_actually_stored(admin_view, subscription, monkeypatch):
    monkeypatch.setattr("nexvpn.admin.panel_sync.sync_subscription", lambda obj: True)
    new_date = subscription.expires_at + dt.timedelta(days=10)
    subscription.expires_at = new_date

    save(admin_view, subscription, FakeRequest())

    assert Subscription.objects.get(pk=subscription.pk).expires_at == new_date


def test_panel_failure_does_not_lose_the_edit(admin_view, subscription, monkeypatch):
    """Панель лежит — правка всё равно сохранена, и её подберёт celery."""
    def boom(obj):
        raise RemnawaveError("панель недоступна")

    monkeypatch.setattr("nexvpn.admin.panel_sync.sync_subscription", boom)
    request = FakeRequest()
    new_date = subscription.expires_at + dt.timedelta(days=10)
    subscription.expires_at = new_date

    save(admin_view, subscription, request)

    stored = Subscription.objects.get(pk=subscription.pk)
    assert stored.expires_at == new_date
    assert stored.panel_status == PanelSyncStatusEnum.PENDING, "иначе celery не подберёт"
    assert "не доехало" in request.messages[0]


def test_panel_refusal_is_reported_not_swallowed(admin_view, subscription, monkeypatch):
    monkeypatch.setattr("nexvpn.admin.panel_sync.sync_subscription", lambda obj: False)
    request = FakeRequest()

    save(admin_view, subscription, request)

    assert "панель отказала" in request.messages[0]


# --- правка подписки через inline в карточке пользователя ---


class FakeFormset:
    """Минимальный набор того, чем пользуется save_formset."""

    def __init__(self, instances, model):
        self._instances = instances
        self.model = model
        self.deleted_objects = []
        self.saved_m2m = False

    def save(self, commit=True):
        return self._instances

    def save_m2m(self):
        self.saved_m2m = True


def test_inline_edit_also_reaches_the_panel(subscription, monkeypatch):
    """Inline сохраняется мимо save_model — без отдельной обработки правка
    осталась бы только у нас, а панель пускала бы по старой дате."""
    from django.contrib.admin.sites import AdminSite

    from nexvpn.admin import NexUserAdmin
    from nexvpn.models import NexUser

    pushed = []
    monkeypatch.setattr("nexvpn.admin.panel_sync.sync_subscription",
                        lambda obj: pushed.append(obj.pk) or True)

    view = NexUserAdmin(NexUser, AdminSite())
    monkeypatch.setattr(view, "message_user", lambda request, text, level=None: None)

    subscription.expires_at = subscription.expires_at + dt.timedelta(days=5)
    formset = FakeFormset([subscription], Subscription)

    view.save_formset(FakeRequest(), form=None, formset=formset, change=True)

    assert pushed == [subscription.pk]
    assert Subscription.objects.get(pk=subscription.pk).panel_status == PanelSyncStatusEnum.PENDING \
        or Subscription.objects.get(pk=subscription.pk).panel_status == PanelSyncStatusEnum.SYNCED


def test_inline_edit_stores_the_new_date(subscription, monkeypatch):
    from django.contrib.admin.sites import AdminSite

    from nexvpn.admin import NexUserAdmin
    from nexvpn.models import NexUser

    monkeypatch.setattr("nexvpn.admin.panel_sync.sync_subscription", lambda obj: True)
    view = NexUserAdmin(NexUser, AdminSite())
    monkeypatch.setattr(view, "message_user", lambda request, text, level=None: None)

    new_date = subscription.expires_at + dt.timedelta(days=5)
    subscription.expires_at = new_date

    view.save_formset(FakeRequest(), form=None, formset=FakeFormset([subscription], Subscription), change=True)

    assert Subscription.objects.get(pk=subscription.pk).expires_at == new_date


def test_other_inlines_are_saved_the_usual_way(monkeypatch):
    """Историю и платежи трогать не надо — они идут обычным путём Django."""
    from django.contrib.admin.sites import AdminSite

    from nexvpn.admin import NexUserAdmin
    from nexvpn.models import NexUser, Payment

    pushed = []
    monkeypatch.setattr("nexvpn.admin.panel_sync.sync_subscription",
                        lambda obj: pushed.append(obj) or True)
    view = NexUserAdmin(NexUser, AdminSite())
    formset = FakeFormset([], Payment)

    view.save_formset(FakeRequest(), form=None, formset=formset, change=True)

    assert pushed == []


def test_user_page_shows_the_subscription_inline():
    """Иначе дату из карточки человека просто негде править."""
    from django.contrib.admin.sites import AdminSite

    from nexvpn.admin import NexUserAdmin, SubscriptionInline
    from nexvpn.models import NexUser

    view = NexUserAdmin(NexUser, AdminSite())

    assert SubscriptionInline in view.inlines
    assert "expires_at" in SubscriptionInline.fields
    assert "expires_at" not in SubscriptionInline.readonly_fields, "дату надо уметь править"


def test_the_user_page_actually_renders(client, subscription):
    """Инлайны ломаются на отрисовке, а не на проверках Django.

    Две связи с NexUser у приглашений, OneToOne у присутствия — каждая из этих
    мелочей роняет страницу целиком, и увидеть это можно только открыв её.
    """
    from django.contrib.auth.models import User

    staff = User.objects.create_superuser("admin-render", "a@example.com", "x")
    client.force_login(staff)

    response = client.get(f"/admin/nexvpn/nexuser/{subscription.user_id}/change/")

    assert response.status_code == 200
    page = response.content.decode()
    for marker in ("История подписки", "Платежи", "Транзакции",
                   "Присутствие в панели", "Кого пригласил", "expires_at"):
        assert marker in page, f"на странице нет блока «{marker}»"
