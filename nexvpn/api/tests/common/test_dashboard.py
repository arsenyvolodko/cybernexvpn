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
