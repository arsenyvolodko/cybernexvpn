"""Переход по реферальной ссылке.

Ссылка выглядит как `t.me/бот?start=<id пригласившего>`. Отказов тут много и
все штатные — важно, чтобы ни один из них не ронял вход в бота: человек в
худшем случае просто не получит бонус, но зайти должен всегда.
"""

import pytest
from asgiref.sync import async_to_sync

from bot.services import register_referral
from nexvpn.api.tests.factories import NexUserFactory, SubscriptionFactory
from nexvpn.models import UserInvitation

pytestmark = pytest.mark.django_db


def call(user, payload):
    return async_to_sync(register_referral)(user, payload)


def test_valid_link_records_the_invitation():
    inviter = NexUserFactory()
    invitee = NexUserFactory()

    assert call(invitee, str(inviter.pk)) is True
    assert UserInvitation.objects.filter(inviter=inviter, invitee=invitee).exists()


def test_own_link_is_ignored():
    """Ссылку на себя скидывают в чат постоянно — это не повод для ошибки."""
    user = NexUserFactory()

    assert call(user, str(user.pk)) is False
    assert not UserInvitation.objects.exists()


def test_second_invitation_does_not_override_the_first():
    first = NexUserFactory()
    second = NexUserFactory()
    invitee = NexUserFactory()
    call(invitee, str(first.pk))

    assert call(invitee, str(second.pk)) is False
    assert UserInvitation.objects.get(invitee=invitee).inviter_id == first.pk


def test_legacy_user_cannot_be_invited():
    """У легаси уже перенесены дни, бонус «за новизну» им не полагается."""
    inviter = NexUserFactory()
    invitee = NexUserFactory(is_legacy=True)

    assert call(invitee, str(inviter.pk)) is False


@pytest.mark.parametrize("payload", ["", "мусор", "0", "99999999999", "12abc", "-5"])
def test_garbage_payload_is_survivable(payload):
    """В ссылку что только не подставляют — падать нельзя."""
    invitee = NexUserFactory()

    assert call(invitee, payload) is False


def test_invitation_alone_grants_nothing():
    """Бонус — только после первой оплаты приглашённого, не за переход."""
    inviter = SubscriptionFactory().user
    invitee = NexUserFactory()
    before = inviter.subscription.expires_at

    call(invitee, str(inviter.pk))

    inviter.subscription.refresh_from_db()
    assert inviter.subscription.expires_at == before
    assert UserInvitation.objects.get().reward_granted_at is None
