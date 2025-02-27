import logging

import aiohttp

from nexvpn.clients import schemas

logger = logging.getLogger(__name__)


class WgAPIClient:

    def __init__(self, config: schemas.ConfigSchema):
        self._base_url = config.url
        self._token = config.api_key
        self._timeout = config.timeout
        self._session = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()

    async def _make_request(self, request: schemas.Request) -> None:
        request.headers["x-api-key"] = f"{self._token}"

        kwargs = {**request.model_dump(by_alias=True), "url": str(request.url)}
        async with self._session.request(**kwargs, timeout=self._timeout) as response:
            response.raise_for_status()

    async def add_client(self, public_key: str, schema: schemas.CreateClientRequest) -> None:
        request = schemas.Request(url=f"{self._base_url}/client/{public_key}", json=schema.model_dump(by_alias=True))
        await self._make_request(request)

    async def delete_client(self, public_key: str) -> None:
        request = schemas.Request(url=f"{self._base_url}/client/{public_key}", method=schemas.Method.DELETE)
        await self._make_request(request)
