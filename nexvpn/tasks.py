import asyncio
import logging
from datetime import datetime

from celery import shared_task
from django.conf import settings

from nexvpn.api_clients import TgBotAPIClient
from nexvpn.api_clients.schemas import ConfigSchema
from nexvpn.api_clients.tg_bot_api_client.schemas import SubscriptionUpdates
from nexvpn.subscription.updates import get_updates_schema


logger = logging.getLogger(__name__)


async def _send_updates_util(updates: SubscriptionUpdates):
    config_schema = ConfigSchema(
        url=settings.TG_BOT_API_URL,
        api_key=settings.TG_BOT_API_KEY
    )
    async with TgBotAPIClient(config_schema) as api_client:
        await api_client.make_subscription_updates(updates)


@shared_task()
def send_updates(date_time: str, is_reminder: bool):
    date_time = datetime.fromisoformat(date_time)
    logger.info(f"Starting updates task. Get date_time: {date_time}, is_reminder: {is_reminder}")
    updates_schema = get_updates_schema(date_time, is_reminder)
    logger.info(f"Sending subscription updates task: {updates_schema}")
    asyncio.run(_send_updates_util(updates_schema))
