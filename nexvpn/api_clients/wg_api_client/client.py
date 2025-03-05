import logging

from nexvpn.api_clients import schemas
from nexvpn.api_clients.base_api_client import BaseAPIClient
from nexvpn.api_clients.wg_api_client.schemas import CreateClientRequest, DeleteClientRequest, CreateClientsRequest, \
    DeleteClientsRequest, ClientRequest

logger = logging.getLogger(__name__)


class WgAPIClient(BaseAPIClient):

    async def _handle_clients_request(
            self,
            schema: ClientRequest,
            method: schemas.Method = schemas.Method.POST,
            many: bool = False
    ) -> None:
        postfix = "clients" if many else "client"
        request = schemas.Request(
            url=f"{self._base_url}/{postfix}/",
            method=method,
            json=schema.model_dump(by_alias=True)
        )
        await self._make_request(request)

    async def add_client(self, schema: CreateClientRequest) -> None:
        await self._handle_clients_request(schema)

    async def delete_client(self, schema: DeleteClientRequest) -> None:
        await self._handle_clients_request(schema, schemas.Method.DELETE)

    async def add_clients(self, schema: CreateClientsRequest) -> None:
        await self._handle_clients_request(schema, many=True)

    async def delete_clients(self, schema: DeleteClientsRequest) -> None:
        await self._handle_clients_request(schema, schemas.Method.DELETE, many=True)
