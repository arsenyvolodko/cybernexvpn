from typing import Callable

from nexvpn.api.exceptions.base_client_error import BaseClientError
from nexvpn.api_clients.schemas import ConfigSchema
from nexvpn.api_clients.telemt_api_client.client import TelemtAPIClient
from nexvpn.api_clients.telemt_api_client.schemas import DeleteTelemtClientRequest, CreateTelemtClientRequest, \
    CreateTelemtClientsRequest, DeleteTelemtClientsRequest, TelemtClientRequest
from nexvpn.models import ProxyClient, ProxyServerConfig

PROXY_BASE_URL = "tg://proxy?server={ip}&port=443&secret=ee{secret}{mask_domain}"


def get_proxy_url(proxy_server: ProxyServerConfig, proxy_client: ProxyClient) -> str:
    return PROXY_BASE_URL.format(
        ip=proxy_server.ip,
        secret=proxy_client.secret,
        mask_domain=proxy_server.mask_domain.encode().hex()
    )


def get_proxy_server_config() -> ProxyServerConfig:
    proxy_server = ProxyServerConfig.objects.first()
    if not proxy_server:
        raise BaseClientError(
            message="No proxy server configured."
        )
    return proxy_server


async def _handle_proxy_clients_util(
        config_schema: ConfigSchema, proxy_client_request: TelemtClientRequest, method: Callable
):
    async with TelemtAPIClient(config_schema) as api_client:
        return await method(api_client, proxy_client_request)


async def add_proxy_client(config_schema: ConfigSchema, create_client_request: CreateTelemtClientRequest):
    await _handle_proxy_clients_util(config_schema, create_client_request, TelemtAPIClient.add_proxy_client)


async def delete_proxy_client(config_schema: ConfigSchema, delete_client_request: DeleteTelemtClientRequest):
    await _handle_proxy_clients_util(config_schema, delete_client_request, TelemtAPIClient.delete_proxy_client)


async def add_proxy_clients(config_schema: ConfigSchema, create_clients_request: CreateTelemtClientsRequest):
    await _handle_proxy_clients_util(config_schema, create_clients_request, TelemtAPIClient.add_proxy_clients)


async def delete_proxy_clients(config_schema: ConfigSchema, delete_clients_request: DeleteTelemtClientsRequest):
    await _handle_proxy_clients_util(config_schema, delete_clients_request, TelemtAPIClient.delete_proxy_clients)
