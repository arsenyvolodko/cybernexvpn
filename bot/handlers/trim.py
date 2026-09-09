"""Переход на тариф с меньшим числом устройств.

Панель не выбрасывает лишние HWID сама — она лишь перестаёт принимать новые.
Поэтому без этого сценария человек оказывался в состоянии «Занято: 3 из 1»:
тариф уменьшился, устройства остались, и подключить новое нельзя, пока не
удалишь что-то вручную в другом разделе.

Два пути, оба заканчиваются одним:

    предупреждение
      ├─ Удалить автоматически → сносим самые давно неактивные
      └─ Выбрать самому → отмечаешь красным → Применить

Выбор хранится в базе, а не в состоянии диалога: любое нажатие кнопки
сбрасывает FSM, а экран должен пережить и это, и перезапуск бота.
"""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot import texts
from bot.handlers.common import render
from bot.keyboards import keyboards
from bot.keyboards.factories import TrimCallback
from bot.services import (
    apply_trim_and_change,
    auto_trim_hwids,
    get_trim_plan,
    remember_trim_target,
    toggle_trim_device,
)
from nexvpn.models import NexUser
from nexvpn.subscription.service import SubscriptionError

logger = logging.getLogger(__name__)

router = Router(name="trim")


def _warning_text(plan) -> str:
    listed = "\n".join(
        f"· {device.title}" + (f" — был {device.last_seen}" if device.last_seen else "")
        for device in plan.oldest
    )
    return texts.TRIM_WARNING.format(
        plan=texts.plural_devices(plan.limit),
        limit=texts.plural_devices(plan.limit),
        used=len(plan.devices),
        excess=texts.plural_devices(plan.to_remove),
        list=listed,
    )


async def show_warning(call: CallbackQuery, user: NexUser, device_limit: int | None = None) -> bool:
    """Показать предупреждение. False — удалять нечего, можно менять тариф."""
    if device_limit is not None:
        await remember_trim_target(user, device_limit)

    plan = await get_trim_plan(user, device_limit)
    if plan is None:
        await render(call, texts.TRIM_PANEL_SILENT, keyboards.only_back())
        return True
    if plan.to_remove == 0:
        return False

    await render(call, _warning_text(plan), keyboards.trim_warning())
    return True


async def _show_picker(call: CallbackQuery, user: NexUser) -> None:
    plan = await get_trim_plan(user)
    if plan is None:
        await render(call, texts.TRIM_PANEL_SILENT, keyboards.only_back())
        return
    text = texts.TRIM_PICK if plan.left_to_pick else texts.TRIM_PICK_ENOUGH
    await render(
        call,
        text.format(left=plan.left_to_pick),
        keyboards.trim_pick(plan.devices, set(plan.selected)),
    )


async def _finish(call: CallbackQuery, user: NexUser, hwids: list[str]) -> None:
    try:
        subscription = await apply_trim_and_change(user, hwids)
    except SubscriptionError as error:
        await call.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Не удалось удалить устройства при смене тарифа")
        await render(call, texts.TRIM_PANEL_SILENT, keyboards.only_back())
        return

    await render(
        call,
        texts.TRIM_DONE.format(
            plan=texts.plural_devices(subscription.plan.device_limit), removed=len(hwids)
        ),
        keyboards.only_back(),
    )


@router.callback_query(TrimCallback.filter(F.action == "warn"))
async def handle_back_to_warning(call: CallbackQuery, user: NexUser) -> None:
    await call.answer()
    if not await show_warning(call, user):
        # Пока человек выбирал, устройств стало не больше лимита — например,
        # он удалил их в другом окне. Предупреждать больше не о чем.
        await _finish(call, user, [])


@router.callback_query(TrimCallback.filter(F.action == "auto"))
async def handle_auto(call: CallbackQuery, user: NexUser) -> None:
    await call.answer()
    await _finish(call, user, await auto_trim_hwids(user))


@router.callback_query(TrimCallback.filter(F.action == "manual"))
async def handle_manual(call: CallbackQuery, user: NexUser) -> None:
    await call.answer()
    await _show_picker(call, user)


@router.callback_query(TrimCallback.filter(F.action == "toggle"))
async def handle_toggle(call: CallbackQuery, callback_data: TrimCallback, user: NexUser) -> None:
    await call.answer()
    await toggle_trim_device(user, callback_data.token)
    await _show_picker(call, user)


@router.callback_query(TrimCallback.filter(F.action == "apply"))
async def handle_apply(call: CallbackQuery, user: NexUser) -> None:
    plan = await get_trim_plan(user)
    if plan is None:
        await call.answer()
        await render(call, texts.TRIM_PANEL_SILENT, keyboards.only_back())
        return

    if plan.left_to_pick:
        # Алертом, а не новым экраном: человек никуда не уходил, ему нужен
        # только намёк, сколько ещё отметить.
        await call.answer(
            texts.TRIM_NEED_MORE.format(left=texts.plural_devices(plan.left_to_pick)),
            show_alert=True,
        )
        return

    await call.answer()
    await _finish(call, user, list(plan.selected))
