"""Экран рефералки показывает актуальные ставки программы.

Дни настраиваются в админке (`GlobalSettings.referral_inviter_days_min` /
`referral_inviter_days_max` / `referral_invitee_days`, см. миграции 0044 и
0045) — экран в боте обязан отражать текущее значение из базы. Дни инвайтера
— вилка (min/max), точная сумма решается только на оплате приглашённого.
"""

import pytest
from asgiref.sync import async_to_sync

from bot.handlers.referral import handle_referral
from nexvpn.api.tests.factories import NexUserFactory
from nexvpn.models import GlobalSettings

pytestmark = pytest.mark.django_db


class FakeChat:
    id = 100


class FakeMessage:
    def __init__(self):
        self.chat = FakeChat()
        self.message_id = 7
        self.photo = self.document = self.video = None
        self.text = None
        self.keyboard = None

    async def edit_text(self, text, reply_markup=None):
        self.text, self.keyboard = text, reply_markup

    async def answer(self, text, reply_markup=None):
        self.text, self.keyboard = text, reply_markup
        return self

    async def delete(self):
        pass

    async def edit_reply_markup(self, reply_markup=None):
        pass


class FakeCall:
    def __init__(self):
        self.message = FakeMessage()

    async def answer(self, text=None, show_alert=False):
        pass


def show(user):
    call = FakeCall()
    async_to_sync(handle_referral)(call, user)
    return call.message.text


def test_screen_shows_the_configured_days_and_they_may_differ():
    """Min — только в фразе «N бесплатных дней» (так в макете владельца).
    Max и дни приглашённого дополнительно встречаются как отдельное «N дней»/
    «N дня» — их вставляет `plural_days`, поэтому проверяем оба места."""
    GlobalSettings.load()
    GlobalSettings.objects.filter(pk=1).update(
        referral_inviter_days_min=25, referral_inviter_days_max=40, referral_invitee_days=4
    )

    text = show(NexUserFactory())

    assert "25 бесплатных дней" in text
    assert "40 дней" in text
    assert "40 бесплатных дней" in text
    assert "4 дня" in text


def test_screen_reflects_a_change_in_admin_settings():
    """Тот же экран, другие ставки в базе — число обязано поменяться следом."""
    user = NexUserFactory()
    GlobalSettings.load()

    GlobalSettings.objects.filter(pk=1).update(
        referral_inviter_days_min=10, referral_inviter_days_max=30, referral_invitee_days=10
    )
    text = show(user)
    assert "10 бесплатных дней" in text
    assert "30 дней" in text

    GlobalSettings.objects.filter(pk=1).update(
        referral_inviter_days_min=15, referral_inviter_days_max=45, referral_invitee_days=5
    )
    text = show(user)
    assert "15 бесплатных дней" in text
    assert "45 дней" in text
    assert "5 дней" in text


def test_default_referral_days_match_the_new_tiered_defaults():
    """Дефолты новой тарифной схемы: 10 дней инвайтеру, если его тариф дороже,
    30 — если не дороже; приглашённому по-прежнему 10, как и было исторически."""
    billing = GlobalSettings.load()

    assert billing.referral_inviter_days_min == 10
    assert billing.referral_inviter_days_max == 30
    assert billing.referral_invitee_days == 10
    text = show(NexUserFactory())
    assert "10 бесплатных дней" in text
    assert "30 дней" in text


def test_admin_fieldsets_reference_real_fields():
    """Ловит опечатку в имени поля — иначе она всплывает только при открытии страницы в браузере."""
    from django.contrib.admin.sites import AdminSite

    from nexvpn.admin import GlobalSettingsAdmin

    assert GlobalSettingsAdmin(GlobalSettings, AdminSite()).check() == []
