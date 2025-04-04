import random
from unittest.mock import patch

import pytest
from dateutil.relativedelta import relativedelta
from django.utils.timezone import now

from nexvpn.api.tests.factories import NexUserFactory, ClientFactory, ServerFactory, UserBalanceFactory
from nexvpn.api_clients.tg_bot_api_client.schemas import SubscriptionUpdates, UserSubscriptionUpdates
from nexvpn.enums import TransactionTypeEnum, TransactionStatusEnum, ClientUpdatesEnum
from nexvpn.models import Client, Transaction, ClientUpdates
from nexvpn.subscription.updates import get_updates_schema

pytestmark = [
    pytest.mark.django_db(),
]

'''
user1:
    balance: 200 -> 0
    clients:
        client1: end_date: in future, active: True, auto-renew: True, price: 50 -> do nothing
        client2: end_date: in future, active: True, auto-renew: False, price: 50 -> do nothing
        
        client3: end_date: today, active: True, auto-renew: True, price: 100 -> renew ok
        client4: end_date: today, active: True, auto-renew: True, price: 50 -> renew ok
        client5: end_date: today, active: True, auto-renew: True, price: 50 -> renew ok
        client6: end_date: today, active: True, auto-renew: True, price: 100 -> stop due to lack of funds
        client7: end_date: today, active: True, auto-renew: False, price: 50 -> stop due to offed auto renew
        
        client8: end_date: 2 months ago, active: False, auto-renew: False, price: 50 -> delete
        client9: end_date: 1 month ago, active: False, auto-renew: False, price: 50 -> do nothing
        
user2:
    balance: 110 -> 10
    clients:
        client1: end_date: today, active: True, auto-renew: True, price: 50 -> renew ok
        client2: end_date: today, active: True, auto-renew: True, price: 50 -> renew ok
        client3: end_date: in future, active: True, auto-renew: True, price: 50 -> do nothing
'''


def create_client(
        user, is_active, auto_renew, end_date, server
) -> Client:
    client = ClientFactory.create(user=user, server=server)
    client.is_active = is_active
    client.auto_renew = auto_renew
    client.end_date = end_date
    client.save()
    return client


def check_results(updates: SubscriptionUpdates, expected: SubscriptionUpdates, is_reminder):
    expected.is_reminder = is_reminder
    assert updates.is_reminder == expected.is_reminder
    assert len(updates.updates) == len(expected.updates)
    updates.updates.sort(key=lambda x: x.user)

    for i, user_updates in enumerate(updates.updates):
        assert user_updates.user == expected.updates[i].user
        assert sorted(user_updates.renewed) == sorted(expected.updates[i].renewed)
        assert sorted(user_updates.stopped_due_to_lack_of_funds) == sorted(
            expected.updates[i].stopped_due_to_lack_of_funds)
        assert sorted(user_updates.stopped_due_to_offed_auto_renew) == sorted(
            expected.updates[i].stopped_due_to_offed_auto_renew)
        assert sorted(user_updates.deleted) == sorted(expected.updates[i].deleted)


def test_subscription_updates():
    today = now()

    yesterday = today - relativedelta(days=1)
    future = today + relativedelta(days=random.randint(1, 10))
    past_1_month = today - relativedelta(months=1)
    past_2_months = today - relativedelta(months=2)
    server50, server100 = ServerFactory.create(price=50), ServerFactory.create(price=100)
    user1, user2 = NexUserFactory(), NexUserFactory()
    user_balance_1 = UserBalanceFactory.create(user=user1, value=210)
    user_balance_2 = UserBalanceFactory.create(user=user2, value=110)

    client1_1 = create_client(user1, is_active=True, auto_renew=True, end_date=future.date(), server=server50)
    client1_2 = create_client(user1, is_active=True, auto_renew=False, end_date=future.date(), server=server50)
    client1_3 = create_client(user1, is_active=True, auto_renew=True, end_date=yesterday.date(), server=server100)
    client1_4 = create_client(user1, is_active=True, auto_renew=True, end_date=today.date(), server=server50)
    client1_5 = create_client(user1, is_active=True, auto_renew=True, end_date=today.date(), server=server50)
    client1_6 = create_client(user1, is_active=True, auto_renew=True, end_date=today.date(), server=server100)
    client1_7 = create_client(user1, is_active=True, auto_renew=False, end_date=today.date(), server=server50)
    client1_8 = create_client(user1, is_active=False, auto_renew=False, end_date=past_2_months.date(), server=server50)
    client1_9 = create_client(user1, is_active=False, auto_renew=False, end_date=past_1_month.date(), server=server50)

    client2_1 = create_client(user2, is_active=True, auto_renew=True, end_date=today.date(), server=server50)
    client2_2 = create_client(user2, is_active=True, auto_renew=True, end_date=today.date(), server=server50)
    client2_3 = create_client(user2, is_active=True, auto_renew=True, end_date=future.date(), server=server50)

    objects_to_refresh = [
        user1, user2,
        user_balance_1, user_balance_2,
        client1_1, client1_2, client1_3, client1_4, client1_5, client1_6, client1_7, client1_8, client1_9,
        client2_1, client2_2, client2_3
    ]

    expected_clients_renewed = [client1_3, client1_4, client1_5, client2_1, client2_2]
    expected_clients_stopped_due_to_lack_of_funds = [client1_6]
    expected_clients_stopped_due_to_offed_auto_renew = [client1_7]
    expected_client_deleted = [client1_8]
    expected_do_nothing = [client1_1, client1_2, client1_9, client2_3]

    expected_results = SubscriptionUpdates(
        is_reminder=False,
        updates=[
            UserSubscriptionUpdates(
                user=user1.id,
                total_price=200,
                renewed=[client1_3.name, client1_4.name, client1_5.name],
                stopped_due_to_lack_of_funds=[client1_6.name],
                stopped_due_to_offed_auto_renew=[client1_7.name],
                deleted=[client1_8.name]
            ),
            UserSubscriptionUpdates(
                user=user2.id,
                total_price=100,
                renewed=[client2_1.name, client2_2.name],
                stopped_due_to_lack_of_funds=[],
                stopped_due_to_offed_auto_renew=[],
                deleted=[]
            )
        ]
    )

    clients_before_reminder = list(Client.objects.all())
    reminder_updates = get_updates_schema(date_time=today, is_reminder=True)
    assert clients_before_reminder == list(Client.objects.all())
    assert Transaction.objects.count() == 0
    assert ClientUpdates.objects.count() == 0

    for obj in objects_to_refresh:
        obj.refresh_from_db()

    check_results(reminder_updates, expected_results, is_reminder=True)

    assert user_balance_1.value == 210
    assert user_balance_2.value == 110

    with patch("nexvpn.utils._handle_clients_util") as mock:
        updates: SubscriptionUpdates = get_updates_schema(date_time=today, is_reminder=False)
        assert mock.call_count == 2

    check_results(updates, expected_results, is_reminder=False)

    client_to_not_refresh = expected_do_nothing + expected_client_deleted
    for client_to_delete in client_to_not_refresh:
        objects_to_refresh.remove(client_to_delete)

    for obj in objects_to_refresh:
        obj.refresh_from_db()

    for client in expected_clients_renewed:
        assert client.is_active is True
        assert client.auto_renew is True
        assert client.end_date == (today + relativedelta(months=1)).date()

    for client in expected_clients_stopped_due_to_lack_of_funds:
        assert client.is_active is False
        assert client.auto_renew is False
        assert client.end_date == today.date()

    for client in expected_clients_stopped_due_to_offed_auto_renew:
        assert client.is_active is False
        assert client.auto_renew is False
        assert client.end_date == today.date()

    for client in expected_client_deleted:
        assert not Client.objects.filter(pk=client.pk).exists()

    for old_client in expected_do_nothing:
        cur_client = Client.objects.get(pk=old_client.pk)
        assert old_client == cur_client

    user1_transactions = Transaction.objects.filter(
        user=user1, is_credit=False, type=TransactionTypeEnum.RENEW_SUBSCRIPTION
    )

    assert user_balance_1.value == 10

    assert user1_transactions.count() == 4
    assert user1_transactions.filter(value=100, status=TransactionStatusEnum.SUCCEEDED).count() == 1
    assert user1_transactions.filter(value=50, status=TransactionStatusEnum.SUCCEEDED).count() == 2
    assert user1_transactions.filter(value=100, status=TransactionStatusEnum.FAILED).count() == 1

    user2_transactions = Transaction.objects.filter(
        user=user2, is_credit=False, type=TransactionTypeEnum.RENEW_SUBSCRIPTION
    )

    assert user_balance_2.value == 10

    assert user2_transactions.count() == 2
    assert user2_transactions.filter(value=50, status=TransactionStatusEnum.SUCCEEDED).count() == 2

    client_updates = ClientUpdates.objects.filter(automatically=True)
    user1_client_updates = client_updates.filter(user=user1)
    assert user1_client_updates.count() == 6
    assert user1_client_updates.filter(action=ClientUpdatesEnum.RENEWED,
                                       client_id__in=[client1_3.id, client1_4.id, client1_5.id]).count() == 3
    assert user1_client_updates.filter(action=ClientUpdatesEnum.SUBSCRIPTION_STOPPED_DUE_TO_LACK_OF_FUNDS,
                                       client_id=client1_6.id).count() == 1
    assert user1_client_updates.filter(action=ClientUpdatesEnum.SUBSCRIPTION_STOPPED_DUE_TO_OFFED_AUTO_RENEW,
                                       client_id=client1_7.id).count() == 1
    assert user1_client_updates.filter(action=ClientUpdatesEnum.DELETED).count() == 1

    user2_client_updates = client_updates.filter(user=user2)
    assert user2_client_updates.count() == 2
    assert user2_client_updates.filter(action=ClientUpdatesEnum.RENEWED,
                                       client_id__in=[client2_1.id, client2_2.id]).count() == 2
