import logging

from nexvpn.api_clients import schemas
from nexvpn.api_clients.base_api_client import BaseAPIClient
from nexvpn.api_clients.telemt_api_client.schemas import CreateTelemtClientRequest, DeleteTelemtClientRequest, \
    CreateTelemtClientsRequest, DeleteTelemtClientsRequest, TelemtClientRequest

logger = logging.getLogger(__name__)


class TelemtAPIClient(BaseAPIClient):

    async def _handle_proxy_clients_request(
        self,
        schema: TelemtClientRequest,
        method: schemas.Method = schemas.Method.POST,
        many: bool = False
    ) -> None:
        postfix = "proxy-clients" if many else "proxy-client"
        request = schemas.Request(
            url=f"{self._base_url}/{postfix}/",
            method=method,
            json=schema.model_dump(by_alias=True)
        )
        await self._make_request(request)

    async def add_proxy_client(self, schema: CreateTelemtClientRequest) -> None:
        await self._handle_proxy_clients_request(schema)

    async def delete_proxy_client(self, schema: DeleteTelemtClientRequest) -> None:
        await self._handle_proxy_clients_request(schema, schemas.Method.DELETE)

    async def add_proxy_clients(self, schema: CreateTelemtClientsRequest) -> None:
        await self._handle_proxy_clients_request(schema, many=True)

    async def delete_proxy_clients(self, schema: DeleteTelemtClientsRequest) -> None:
        await self._handle_proxy_clients_request(schema, schemas.Method.DELETE, many=True)
