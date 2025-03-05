import uuid
from typing import Any, Callable

from django.conf import settings

from nexvpn.api_clients import WgAPIClient, TgBotAPIClient
from nexvpn.api_clients.schemas import ConfigSchema
from nexvpn.api_clients.wg_api_client.schemas import CreateClientRequest, DeleteClientRequest, CreateClientsRequest, \
    DeleteClientsRequest, ClientRequest
from nexvpn.models import Client, ServerConfig


def get_config_schema(client: Client) -> ConfigSchema:
    server_config = client.server.config
    return ConfigSchema(url=server_config.base_url, api_key=server_config.api_key)


async def _handle_clients_util(
    config_schema: ConfigSchema, client_request: ClientRequest, method: Callable
):
    async with WgAPIClient(config_schema) as api_client:
        return await method(api_client, client_request)


async def add_client(config_schema: ConfigSchema, create_client_request: CreateClientRequest):
    await _handle_clients_util(config_schema, create_client_request, WgAPIClient.add_client)


async def delete_client(config_schema: ConfigSchema, delete_client_request: DeleteClientRequest):
    await _handle_clients_util(config_schema, delete_client_request, WgAPIClient.delete_client)


async def add_clients(config_schema: ConfigSchema, create_clients_request: CreateClientsRequest):
    await _handle_clients_util(config_schema, create_clients_request, WgAPIClient.add_clients)


async def delete_clients(config_schema: ConfigSchema, delete_clients_request: DeleteClientsRequest):
    await _handle_clients_util(config_schema, delete_clients_request, WgAPIClient.delete_clients)


async def succeed_payment(payment_id: uuid):
    config_schema = ConfigSchema(url=settings.TG_BOT_API_URL, api_key=settings.TG_BOT_API_KEY)
    async with TgBotAPIClient(config_schema) as api_client:
        await api_client.succeed_payment(payment_id)


def gen_client_config_data(client: Client) -> str:
    server_config: ServerConfig = client.server.config
    file_data = (
        "[Interface]\n"
        f"PrivateKey = {client.private_key}\n"
        f"Address = {client.endpoint.ip}\n"
        "DNS = 1.1.1.1\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {server_config.wg_public_key}\n"
        "AllowedIPs = 0.0.0.0/0\n"
        f"Endpoint = {server_config.ip}:{server_config.wg_listen_port}"
    )
    return file_data


def gen_yookassa_payment_data(value: int) -> dict[str, Any]:
    payment_data = {
        "amount": {"value": f"{value}.00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": settings.TG_BOT_URL,
        },
        "capture": True,
        "description": f"Пополнение баланса на {value} рублей"
    }

    return payment_data
