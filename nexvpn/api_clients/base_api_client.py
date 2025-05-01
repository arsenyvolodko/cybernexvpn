import logging
from abc import ABC

import aiohttp

from nexvpn.api_clients import schemas

logger = logging.getLogger(__name__)


class BaseAPIClient(ABC):

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
        logger.info(f"Making request: {kwargs}")
        async with self._session.request(**kwargs, timeout=self._timeout) as response:
            logger.info(f"Response status: {response.status}")
            response.raise_for_status()
