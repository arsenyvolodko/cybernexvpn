from typing import Any

from django.conf import settings

from nexvpn.api_clients.schemas import ConfigSchema, CreateClientRequest
from nexvpn.api_clients.wg_api_client import WgAPIClient
from nexvpn.models import Client, ServerConfig


def get_config_schema(client: Client) -> ConfigSchema:
    server_config = client.server.config
    return ConfigSchema(url=server_config.base_url, api_key=server_config.api_key)


async def add_client(config_schema: ConfigSchema, public_key: str, create_client_request: CreateClientRequest):
    async with WgAPIClient(config_schema) as api_client:
        await api_client.add_client(public_key, create_client_request)


async def delete_client(config_schema, public_key: str):
    async with WgAPIClient(config_schema) as api_client:
        await api_client.delete_client(public_key)


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
