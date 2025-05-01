import asyncio
import datetime
from collections import defaultdict

from dateutil.relativedelta import relativedelta
from django.utils.timezone import now

from cybernexvpn.base_model import BaseModel
from nexvpn.api_clients.schemas import ConfigSchema
from nexvpn.api_clients.tg_bot_api_client.schemas import UserSubscriptionUpdates, SubscriptionUpdates
from nexvpn.api_clients.wg_api_client.schemas import DeleteClientsRequest, DeleteClientRequest
from nexvpn.enums import TransactionTypeEnum, TransactionStatusEnum, ClientUpdatesEnum
from nexvpn.models import Client, UserBalance, Transaction, Server, ClientUpdates
from nexvpn.utils import delete_clients


class ClientsSchema(BaseModel):
    clients_to_renew: list[Client]
    extra_clients_to_stop: list[Client]
    clients_to_stop: list[Client]
    clients_to_delete: list[Client]


def get_response_schema(user_id: int, total_price: int, clients_schema: ClientsSchema) -> UserSubscriptionUpdates:

    return UserSubscriptionUpdates(
        user=user_id,
        total_price=total_price,
        renewed=[client.name for client in clients_schema.clients_to_renew],
        stopped_due_to_lack_of_funds=[client.name for client in clients_schema.extra_clients_to_stop],
        stopped_due_to_offed_auto_renew=[client.name for client in clients_schema.clients_to_stop],
        deleted=[client.name for client in clients_schema.clients_to_delete]
    )


def update_clients_to_renew(user_id: int, clients_to_renew: list[Client]) -> tuple[list[Client], list[Client]]:
    new_clients_to_renew = list()
    clients_to_renew.sort(key=lambda x: x.server.price)
    cur_user_balance = UserBalance.objects.get(user_id=user_id).value
    while clients_to_renew and cur_user_balance >= clients_to_renew[0].server.price:
        cur_user_balance -= clients_to_renew[0].server.price
        new_clients_to_renew.append(clients_to_renew.pop(0))
    extra_clients_to_stop = clients_to_renew
    return new_clients_to_renew, extra_clients_to_stop


def get_clients_by_groups(user_id: int, date_time: datetime) -> ClientsSchema:
    user_clients = Client.objects.filter(user_id=user_id)
    clients_to_renew_list = list(user_clients.filter(is_active=True, auto_renew=True, end_date__lte=date_time.date()))
    clients_to_stop_list = list(user_clients.filter(is_active=True, auto_renew=False, end_date__lte=date_time.date()))
    clients_to_delete_list = list(
        user_clients.filter(is_active=False, end_date__lte=(date_time - relativedelta(months=2)).date()))
    clients_to_renew_list, extra_clients_to_stop = update_clients_to_renew(user_id, clients_to_renew_list)

    return ClientsSchema(
        clients_to_renew=clients_to_renew_list, extra_clients_to_stop=extra_clients_to_stop,
        clients_to_stop=clients_to_stop_list, clients_to_delete=clients_to_delete_list
    )


def get_users_with_clients_to_update(date_time: datetime, is_reminder: bool) -> list[int]:
    filter_name = "end_date" if is_reminder else "end_date__lte"
    filters = {
        "is_active": True,
        filter_name: date_time.date(),
    }

    users_with_clients_to_update = (
        Client.objects
        .filter(**filters)
        .values_list("user", flat=True)
        .distinct()
    )
    return list(users_with_clients_to_update)


def handle_clients_to_renew(user_id: int, clients_to_renew: list[Client]) -> int:
    total_price = 0
    for client in clients_to_renew:
        client.end_date = (now() + relativedelta(months=1)).date()
        client.save()

        client_price = client.server.price

        user_balance = UserBalance.objects.get(user_id=user_id)
        user_balance.value -= client_price
        total_price += client_price
        user_balance.save()

        ClientUpdates.objects.create(
            user_id=user_id,
            client=client,
            action=ClientUpdatesEnum.RENEWED,
            automatically=True
        )

        Transaction.objects.create(
            user_id=user_id,
            is_credit=False,
            value=client_price,
            type=TransactionTypeEnum.RENEW_SUBSCRIPTION,
        )

    return total_price


def handle_clients_to_stop(user_id: int, clients_to_stop: list[Client], due_to_lack_of_funds: bool):
    for client in clients_to_stop:
        client.is_active = False
        client.auto_renew = False
        client.save()

        action = None
        if due_to_lack_of_funds:
            Transaction.objects.create(
                user_id=user_id,
                is_credit=False,
                value=client.server.price,
                type=TransactionTypeEnum.RENEW_SUBSCRIPTION,
                status=TransactionStatusEnum.FAILED
            )
            action = ClientUpdatesEnum.SUBSCRIPTION_STOPPED_DUE_TO_LACK_OF_FUNDS

        ClientUpdates.objects.create(
            user_id=user_id,
            client=client,
            action=action or ClientUpdatesEnum.SUBSCRIPTION_STOPPED_DUE_TO_OFFED_AUTO_RENEW,
            automatically=True
        )


def handle_clients_to_delete(clients_to_delete: list[Client]):
    for client in clients_to_delete:

        ClientUpdates.objects.create(
            user=client.user,
            client=None,
            action=ClientUpdatesEnum.DELETED,
            automatically=True
        )

        client.delete()


def delete_clients_from_server(client_to_delete: list[Client]):
    servers: set[Server] = set(client.server for client in client_to_delete)
    server_id_clients: dict[int, list[Client]] = defaultdict(list)
    for client in client_to_delete:
        server_id_clients[client.server.id].append(client)

    for server in servers:
        config = server.config
        clients = server_id_clients[server.id]
        config_schema = ConfigSchema(url=config.base_url, api_key=config.api_key)
        delete_clients_request = DeleteClientsRequest(
            clients=[DeleteClientRequest(public_key=client.public_key) for client in clients]
        )
        asyncio.run(delete_clients(config_schema, delete_clients_request))


def handle_clients(user_id: int, client_schema: ClientsSchema) -> tuple[int, list[Client]]:
    total_price = handle_clients_to_renew(user_id, client_schema.clients_to_renew)
    handle_clients_to_stop(user_id, client_schema.extra_clients_to_stop, due_to_lack_of_funds=True)
    handle_clients_to_stop(user_id, client_schema.clients_to_stop, due_to_lack_of_funds=False)
    handle_clients_to_delete(client_schema.clients_to_delete)
    return total_price, client_schema.clients_to_stop + client_schema.extra_clients_to_stop + client_schema.clients_to_delete


def get_updates_schema(date_time: datetime, is_reminder: bool) -> SubscriptionUpdates:
    users_subscriptions_updates: list[UserSubscriptionUpdates] = list()

    clients_to_delete = []
    users_with_clients_to_update = get_users_with_clients_to_update(date_time, is_reminder)

    for user_id in users_with_clients_to_update:
        client_schema: ClientsSchema = get_clients_by_groups(user_id, date_time)
        total_price = 0

        if not is_reminder:
            total_price, extra_clients_to_delete = handle_clients(user_id, client_schema)
            clients_to_delete.extend(extra_clients_to_delete)

        user_updates: UserSubscriptionUpdates = get_response_schema(user_id, total_price, client_schema)
        users_subscriptions_updates.append(user_updates)

    if clients_to_delete:
        delete_clients_from_server(clients_to_delete)
    subscription_updates = SubscriptionUpdates(is_reminder=is_reminder, updates=users_subscriptions_updates)
    return subscription_updates
