import datetime

from dateutil.relativedelta import relativedelta
from django.utils.timezone import now

from cybernexvpn.base_model import BaseModel
from nexvpn.api_clients.schemas import ConfigSchema, UserSubscriptionUpdates, SubscriptionUpdates
from nexvpn.enums import TransactionTypeEnum, TransactionStatusEnum
from nexvpn.models import Client, UserBalance, Transaction


class ClientsSchema(BaseModel):
    clients_to_renew: list[Client]
    extra_clients_to_stop: list[Client]
    clients_to_stop: list[Client]
    clients_to_delete: list[Client]


def get_response_schema(user_id: int, clients_schema: ClientsSchema) -> UserSubscriptionUpdates:
    return UserSubscriptionUpdates(
        user=user_id,
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


def get_users_with_clients_to_update(date_time: datetime) -> list[int]:
    users_with_clients_to_update = (
        Client.objects
        .filter(is_active=True, end_date=date_time.date())
        .values_list("user", flat=True)
        .distinct()
    )
    return list(users_with_clients_to_update)


def handle_clients_to_renew(user_id: int, clients_to_renew: list[Client]):
    for client in clients_to_renew:
        client.end_date = (now() + relativedelta(months=1)).date()
        client.save()

        client_price = client.server.price

        user_balance = UserBalance.objects.get(user_id=user_id)
        user_balance.value -= client_price
        user_balance.save()

        # todo: add table

        Transaction.objects.create(
            user_id=user_id,
            is_credit=False,
            value=client_price,
            type=TransactionTypeEnum.RENEW_SUBSCRIPTION,
        )


def handle_clients_to_stop(user_id: int, clients_to_stop: list[Client], due_to_lack_of_funds: bool):
    for client in clients_to_stop:
        client.is_active = False
        client.auto_renew = False
        client.save()

        # todo: add table

        if due_to_lack_of_funds:
            Transaction.objects.create(
                user_id=user_id,
                is_credit=True,
                value=client.server.price,
                type=TransactionTypeEnum.RENEW_SUBSCRIPTION,
                status=TransactionStatusEnum.FAILED
            )


def handle_clients_to_delete(clients_to_delete: list[Client]):
    for client in clients_to_delete:
        client.delete()


def delete_clients_from_server(client_to_delete: list[Client]):
    servers = set(client.server for client in client_to_delete)
    for server in servers:
        config = server.config
        config_schema = ConfigSchema(url=config.base_url, api_key=config.api_key)
        # todo


def handle_clients(user_id: int, client_schema: ClientsSchema):
    handle_clients_to_renew(user_id, client_schema.clients_to_renew)
    handle_clients_to_stop(user_id, client_schema.extra_clients_to_stop, due_to_lack_of_funds=True)
    handle_clients_to_stop(user_id, client_schema.clients_to_stop, due_to_lack_of_funds=False)
    handle_clients_to_delete(client_schema.clients_to_delete)
    delete_clients_from_server(
        client_schema.clients_to_stop + client_schema.extra_clients_to_stop + client_schema.clients_to_delete)


def get_updates_schema(date_time: datetime, is_reminder: bool) -> SubscriptionUpdates:
    users_subscriptions_updates: list[UserSubscriptionUpdates] = list()

    users_with_clients_to_update = get_users_with_clients_to_update(date_time)
    for user_id in users_with_clients_to_update:
        # todo: add transaction and handle user balance correctly
        client_schema: ClientsSchema = get_clients_by_groups(user_id, date_time)

        if not is_reminder:
            handle_clients(user_id, client_schema)

        user_updates: UserSubscriptionUpdates = get_response_schema(user_id, client_schema)
        users_subscriptions_updates.append(user_updates)

    subscription_updates = SubscriptionUpdates(is_reminder=is_reminder, updates=users_subscriptions_updates)
    return subscription_updates
