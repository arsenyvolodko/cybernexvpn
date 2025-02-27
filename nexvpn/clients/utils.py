from nexvpn.clients.schemas import ConfigSchema, CreateClientRequest
from nexvpn.clients.wg_api_client import WgAPIClient
from nexvpn.models import Client


def get_config_schema(client: Client) -> ConfigSchema:
    server_config = client.server.config
    return ConfigSchema(url=server_config.base_url, api_key=server_config.api_key)


async def add_client(config_schema: ConfigSchema, public_key: str, create_client_request: CreateClientRequest):
    async with WgAPIClient(config_schema) as api_client:
        await api_client.add_client(public_key, create_client_request)


async def delete_client(config_schema, public_key: str):
    async with WgAPIClient(config_schema) as api_client:
        await api_client.delete_client(public_key)
