import logging

from nexvpn.api_clients import schemas
from nexvpn.api_clients.base_api_client import BaseAPIClient

logger = logging.getLogger(__name__)


class WgAPIClient(BaseAPIClient):

    async def add_client(self, public_key: str, schema: schemas.CreateClientRequest) -> None:
        request = schemas.Request(url=f"{self._base_url}/client/{public_key}", json=schema.model_dump(by_alias=True))
        await self._make_request(request)

    async def delete_client(self, public_key: str) -> None:
        request = schemas.Request(url=f"{self._base_url}/client/{public_key}", method=schemas.Method.DELETE)
        await self._make_request(request)
