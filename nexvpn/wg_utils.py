from typing import Callable

from nexvpn.api_clients import WgAPIClient
from nexvpn.api_clients.schemas import ConfigSchema
from nexvpn.api_clients.wg_api_client.schemas import CreateWgClientRequest, DeleteWgClientRequest, CreateClientsRequestWg, \
    DeleteClientsRequestWg, WgClientRequest
from nexvpn.models import Client, ServerConfig, ProxyServerConfig


def get_telemt_config_schema(proxy_server_config: ProxyServerConfig) -> ConfigSchema:
    return ConfigSchema(url=f"http://{proxy_server_config.ip}", api_key=proxy_server_config.api_key)


def get_wg_config_schema(client: Client) -> ConfigSchema:
    server_config = client.server.config
    return ConfigSchema(url=server_config.base_url, api_key=server_config.api_key)


async def _handle_clients_util(
    config_schema: ConfigSchema, client_request: WgClientRequest, method: Callable
):
    async with WgAPIClient(config_schema) as api_client:
        return await method(api_client, client_request)


async def add_client(config_schema: ConfigSchema, create_client_request: CreateWgClientRequest):
    await _handle_clients_util(config_schema, create_client_request, WgAPIClient.add_client)


async def delete_client(config_schema: ConfigSchema, delete_client_request: DeleteWgClientRequest):
    await _handle_clients_util(config_schema, delete_client_request, WgAPIClient.delete_client)


async def add_clients(config_schema: ConfigSchema, create_clients_request: CreateClientsRequestWg):
    await _handle_clients_util(config_schema, create_clients_request, WgAPIClient.add_clients)


async def delete_clients(config_schema: ConfigSchema, delete_clients_request: DeleteClientsRequestWg):
    await _handle_clients_util(config_schema, delete_clients_request, WgAPIClient.delete_clients)


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

