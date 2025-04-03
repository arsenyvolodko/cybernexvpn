import logging

from nexvpn.api_clients import schemas
from nexvpn.api_clients.base_api_client import BaseAPIClient
from nexvpn.api_clients.tg_bot_api_client.schemas import SubscriptionUpdates

logger = logging.getLogger(__name__)


class TgBotAPIClient(BaseAPIClient):

    async def succeed_payment(self, payment_id: str, user_id: int) -> None:
        request = schemas.Request(url=f"{self._base_url}/succeed-payment/{user_id}/{payment_id}")
        await self._make_request(request)

    async def make_subscription_updates(self, updates: SubscriptionUpdates):
        request = schemas.Request(url=f"{self._base_url}/make-subscription-updates/", json=updates.model_dump(by_alias=True))
        await self._make_request(request)
