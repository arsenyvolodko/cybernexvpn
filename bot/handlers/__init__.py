from aiogram import Router

from bot.handlers import (
    billing,
    broadcast,
    channel,
    connect,
    devices,
    faq,
    menu,
    referral,
    subscription,
    support,
)


def build_router() -> Router:
    """Порядок важен: legacy-роутер ловит всё нераспознанное, он последний."""
    root = Router(name="root")
    root.include_router(menu.router)
    root.include_router(subscription.router)
    root.include_router(connect.router)
    root.include_router(billing.router)
    root.include_router(devices.router)
    root.include_router(referral.router)
    root.include_router(faq.router)
    root.include_router(support.router)
    root.include_router(broadcast.router)
    root.include_router(channel.router)
    root.include_router(menu.legacy_router)
    return root
