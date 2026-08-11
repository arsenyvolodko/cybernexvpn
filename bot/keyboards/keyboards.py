"""Клавиатуры.

Правило навигации: на каждом экране, кроме главного меню, внизу строка возврата.
«Назад» ведёт на предыдущий экран, а не в меню — иначе из глубины пришлось бы
прокликивать путь заново. Со второго уровня вложенности рядом появляется
«В меню»: там «Назад» уже не выводит наружу за одно нажатие.
"""

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from bot.apps_catalog import CATALOG, Platform
from bot.keyboards.button import Button
from bot.keyboards.factories import (
    ConnectCallback,
    DeviceCallback,
    FaqCallback,
    PlanCallback,
    RenewCallback,
)
from bot.keyboards.storage import ButtonsStorage

BACK_TEXT = "Назад"


def _back_button(target: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=BACK_TEXT, callback_data=target)


def _nav_row(back_to: str) -> list[InlineKeyboardButton]:
    row = [_back_button(back_to)]
    # Если «Назад» и так ведёт в меню, вторая кнопка была бы дубликатом.
    if back_to != MENU:
        row.append(ButtonsStorage.MAIN_MENU.get_button())
    return row


def _rows(*items, back_to: str | None = None) -> InlineKeyboardMarkup:
    keyboard = [
        [item.get_button() if isinstance(item, Button) else item]
        for item in items
        if item is not None
    ]
    if back_to:
        keyboard.append(_nav_row(back_to))
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _grid(items: list[InlineKeyboardButton], columns: int, back_to: str | None = None) -> InlineKeyboardMarkup:
    keyboard = [items[i : i + columns] for i in range(0, len(items), columns)]
    if back_to:
        keyboard.append(_nav_row(back_to))
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# --- цели навигации ---

MENU = ButtonsStorage.MAIN_MENU.callback
SUBSCRIPTION = ButtonsStorage.MY_SUBSCRIPTION.callback
DEVICES = ButtonsStorage.MY_DEVICES.callback
CONNECT = ButtonsStorage.CONNECT.callback
FAQ = ButtonsStorage.FAQ_SUPPORT.callback


def main_menu() -> InlineKeyboardMarkup:
    """Подключение — первым: это то, зачем человек открыл бота."""
    return _rows(
        ButtonsStorage.CONNECT,
        ButtonsStorage.MY_SUBSCRIPTION,
        ButtonsStorage.REFERRAL,
        ButtonsStorage.FAQ_SUPPORT,
    )


def welcome() -> InlineKeyboardMarkup:
    """Ровно одна кнопка: у новичка не должно быть выбора, куда нажать."""
    return _rows(ButtonsStorage.CONNECT)


def only_back(back_to: str = MENU) -> InlineKeyboardMarkup:
    return _rows(back_to=back_to)


def subscription(*, is_active: bool, web_url: str | None, can_add_device: bool) -> InlineKeyboardMarkup:
    """У истёкшей подписки «Подключиться» и «Сменить тариф» смысла не имеют —
    сначала продление. Кнопка, которая гарантированно откажет, хуже её отсутствия."""
    items: list = []
    if is_active:
        items.append(ButtonsStorage.CONNECT if can_add_device else None)
        items.append(ButtonsStorage.MY_DEVICES)
        items.append(ButtonsStorage.CHANGE_PLAN)
    items.append(ButtonsStorage.RENEW)
    if web_url:
        items.append(InlineKeyboardButton(text=ButtonsStorage.WEB_VERSION.text, url=web_url))
    return _rows(*items, back_to=MENU)


# --- устройства ---


def devices(device_list, *, can_add: bool) -> InlineKeyboardMarkup:
    items = [
        InlineKeyboardButton(
            text=device.title,
            callback_data=DeviceCallback(token=device.token, action="open").pack(),
        )
        for device in device_list
    ]
    if can_add:
        items.append(ButtonsStorage.ADD_DEVICE)
    return _rows(*items, back_to=SUBSCRIPTION)


def device_detail(token: str) -> InlineKeyboardMarkup:
    return _rows(
        InlineKeyboardButton(
            text=ButtonsStorage.DELETE_DEVICE.text,
            callback_data=DeviceCallback(token=token, action="delete").pack(),
        ),
        back_to=DEVICES,
    )


def device_delete_confirm(token: str) -> InlineKeyboardMarkup:
    return _rows(
        InlineKeyboardButton(
            text=ButtonsStorage.DELETE_DEVICE_CONFIRM.text,
            callback_data=DeviceCallback(token=token, action="confirm").pack(),
        ),
        back_to=DeviceCallback(token=token, action="open").pack(),
    )


# --- подключение ---


def platforms() -> InlineKeyboardMarkup:
    return _grid(
        [
            InlineKeyboardButton(
                text=guide.title,
                callback_data=ConnectCallback(platform=platform.value, step="download").pack(),
            )
            for platform, guide in CATALOG.items()
        ],
        columns=2,
        back_to=MENU,
    )


def platform_download(platform: Platform) -> InlineKeyboardMarkup:
    """Ссылки на установку и «Скачал» — одним экраном, без промежуточного выбора."""
    guide = CATALOG[platform]
    return _rows(
        *[InlineKeyboardButton(text=link.text, url=link.url) for link in guide.downloads],
        InlineKeyboardButton(
            text=ButtonsStorage.DOWNLOADED.text,
            callback_data=ConnectCallback(platform=platform.value, step="connect").pack(),
        ),
        back_to=CONNECT,
    )


def platform_connect(platform: Platform, connect_url: str, subscription_url: str) -> InlineKeyboardMarkup:
    """«Скопировать ключ» — штатная кнопка Telegram: копирует в буфер по нажатию,
    без отдельного сообщения с текстом ключа."""
    return _rows(
        InlineKeyboardButton(text=ButtonsStorage.ADD_SUBSCRIPTION.text, url=connect_url),
        InlineKeyboardButton(
            text=ButtonsStorage.COPY_KEY.text,
            copy_text=CopyTextButton(text=subscription_url),
        ),
        back_to=ConnectCallback(platform=platform.value, step="download").pack(),
    )


# --- прочее ---


def referral(link: str) -> InlineKeyboardMarkup:
    """«Поделиться» открывает выбор чата прямо в Telegram."""
    return _rows(
        InlineKeyboardButton(
            text=ButtonsStorage.SHARE_REFERRAL.text,
            url=f"https://t.me/share/url?url={link}",
        ),
        back_to=MENU,
    )


def faq(topics) -> InlineKeyboardMarkup:
    """Раскрывающиеся разделы: каждый — отдельный экран, а не простыня текста."""
    items = [
        InlineKeyboardButton(text=topic.button, callback_data=FaqCallback(topic=topic.key).pack())
        for topic in topics
    ]
    items.append(ButtonsStorage.SUPPORT)
    return _rows(*items, back_to=MENU)


def reminder() -> InlineKeyboardMarkup:
    """Напоминание приходит отдельным сообщением, поэтому «Назад» тут нет —
    возвращаться некуда, есть только действие."""
    return _rows(ButtonsStorage.RENEW, ButtonsStorage.MY_SUBSCRIPTION)


def renew(options) -> InlineKeyboardMarkup:
    """Сроки со скидкой. Выгода прямо в кнопке — иначе её никто не заметит."""
    items = []
    for option in options:
        label = f"{option.months} мес. — {option.price}₽"
        if option.saving:
            label += f"  (−{option.saving}₽)"
        items.append(
            InlineKeyboardButton(text=label, callback_data=RenewCallback(months=option.months).pack())
        )
    return _rows(*items, back_to=SUBSCRIPTION)


def plan_list(options) -> InlineKeyboardMarkup:
    items = []
    for option in options:
        if option.is_current:
            continue
        items.append(
            InlineKeyboardButton(
                text=f"{option.name} — {option.price_month}₽/мес",
                callback_data=PlanCallback(device_limit=option.device_limit, action="open").pack(),
            )
        )
    return _rows(*items, back_to=SUBSCRIPTION)


def plan_change(option) -> InlineKeyboardMarkup:
    """У повышения два honest-варианта, у понижения — один отложенный."""
    items = [
        InlineKeyboardButton(
            text=ButtonsStorage.CHANGE_PLAN_FREE.text,
            callback_data=PlanCallback(device_limit=option.device_limit, action="free").pack(),
        )
    ]
    if option.topup_price:
        items.append(
            InlineKeyboardButton(
                text=f"{ButtonsStorage.CHANGE_PLAN_PAY.text} — {option.topup_price}₽",
                callback_data=PlanCallback(device_limit=option.device_limit, action="pay").pack(),
            )
        )
    return _rows(*items, back_to=ButtonsStorage.CHANGE_PLAN.callback)


def pay(url: str) -> InlineKeyboardMarkup:
    return _rows(
        InlineKeyboardButton(text=ButtonsStorage.PAY.text, url=url),
        back_to=SUBSCRIPTION,
    )


def connected() -> InlineKeyboardMarkup:
    """Экран после успешного подключения: дальше человеку нужны устройства или меню."""
    return _rows(ButtonsStorage.MY_DEVICES, back_to=MENU)


def faq_section() -> InlineKeyboardMarkup:
    return only_back(FAQ)
