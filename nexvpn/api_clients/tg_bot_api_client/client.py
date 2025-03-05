import logging
import uuid

from nexvpn.api_clients import schemas
from nexvpn.api_clients.base_api_client import BaseAPIClient

logger = logging.getLogger(__name__)


class TgBotAPIClient(BaseAPIClient):

    async def succeed_payment(self, payment_id: uuid) -> None:
        request = schemas.Request(url=f"{self._base_url}/succeed-payment/{payment_id}")
        await self._make_request(request)
