from typing import Any

from django.conf import settings

from nexvpn.api_clients import TgBotAPIClient
from nexvpn.api_clients.schemas import ConfigSchema


async def succeed_payment(payment_id: str, user_id: int):
    config_schema = ConfigSchema(url=settings.TG_BOT_API_URL, api_key=settings.TG_BOT_API_KEY)
    async with TgBotAPIClient(config_schema) as api_client:
        await api_client.succeed_payment(payment_id, user_id)


def gen_yookassa_payment_data(value: int) -> dict[str, Any]:
    payment_data = {
        "amount": {"value": f"{value}.00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": settings.TG_BOT_URL,
        },
        "capture": True,
        "description": f"Пополнение баланса на {value} рублей"
    }

    return payment_data
