import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot import texts
from bot.handlers.common import render
from bot.keyboards import ButtonsStorage, keyboards
from bot.keyboards.factories import DeviceCallback
from bot.services import (
    Device,
    delete_device,
    get_subscription_view,
    list_devices,
)
from nexvpn.models import NexUser
from nexvpn.remnawave import RemnawaveError

logger = logging.getLogger(__name__)

router = Router(name="devices")


def _find(device_list: list[Device], token: str) -> Device | None:
    return next((device for device in device_list if device.token == token), None)


@router.callback_query(F.data == ButtonsStorage.MY_DEVICES.callback)
async def handle_my_devices(call: CallbackQuery, user: NexUser) -> None:
    await call.answer()
    await _render_devices(call, user)


async def _render_devices(call: CallbackQuery, user: NexUser) -> None:
    view = await get_subscription_view(user)
    device_list = await list_devices(user)

    if device_list is None:
        await render(call, texts.PANEL_UNAVAILABLE, keyboards.only_back())
        return

    limit = view.device_limit
    can_add = len(device_list) < limit

    if not device_list:
        text = texts.DEVICES_EMPTY
    else:
        text = texts.DEVICES_LIST.format(used=len(device_list), limit=limit)
        if not can_add:
            text += texts.DEVICES_FULL_HINT

    await render(call, text, keyboards.devices(device_list, can_add=can_add))


@router.callback_query(DeviceCallback.filter(F.action == "open"))
async def handle_device(call: CallbackQuery, callback_data: DeviceCallback, user: NexUser) -> None:
    await call.answer()
    device_list = await list_devices(user)
    if device_list is None:
        await render(call, texts.PANEL_UNAVAILABLE, keyboards.only_back())
        return

    device = _find(device_list, callback_data.token)
    if device is None:
        await render(call, texts.DEVICE_ALREADY_GONE, keyboards.only_back())
        return

    await render(
        call,
        texts.DEVICE_DETAIL.format(title=device.title, last_seen=device.last_seen or "неизвестно"),
        keyboards.device_detail(device.token),
    )


@router.callback_query(DeviceCallback.filter(F.action == "delete"))
async def handle_device_delete(call: CallbackQuery, callback_data: DeviceCallback, user: NexUser) -> None:
    """Спрашиваем подтверждение: удаление разрывает доступ сразу."""
    await call.answer()
    device_list = await list_devices(user)
    device = _find(device_list or [], callback_data.token)
    if device is None:
        await render(call, texts.DEVICE_ALREADY_GONE, keyboards.only_back())
        return

    await render(
        call,
        texts.DEVICE_DELETE_CONFIRM.format(title=device.title),
        keyboards.device_delete_confirm(device.token),
    )


@router.callback_query(DeviceCallback.filter(F.action == "confirm"))
async def handle_device_delete_confirm(
    call: CallbackQuery, callback_data: DeviceCallback, user: NexUser
) -> None:
    try:
        deleted = await delete_device(user, callback_data.token)
    except RemnawaveError as exc:
        logger.warning("Не удалось удалить устройство у %s: %s", user.pk, exc)
        await call.answer(texts.PANEL_UNAVAILABLE, show_alert=True)
        return

    # Всплывашка вместо отдельного сообщения: экран сразу перерисуется списком,
    # и подтверждение «удалено» не оставит в чате мусора.
    await call.answer(texts.DEVICE_DELETED if deleted else texts.DEVICE_ALREADY_GONE)
    await _render_devices(call, user)
