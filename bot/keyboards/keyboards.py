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
    ExpiredCallback,
    TrimCallback,
    DeviceCallback,
    FaqCallback,
    PlanCallback,
    RenewCallback,
)
from bot.keyboards.storage import ButtonsStorage

BACK_TEXT = "Назад"


def _back_button(target: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=BACK_TEXT, callback_data=target)


def _nav_row(back_to: str, with_menu: bool = True) -> list[InlineKeyboardButton]:
    row = [_back_button(back_to)]
    # Если «Назад» и так ведёт в меню, вторая кнопка была бы дубликатом.
    if with_menu and back_to != MENU:
        row.append(ButtonsStorage.MAIN_MENU.get_button())
    return row


def _rows(*items, back_to: str | None = None, with_menu: bool = True) -> InlineKeyboardMarkup:
    keyboard = [
        [item.get_button() if isinstance(item, Button) else item]
        for item in items
        if item is not None
    ]
    if back_to:
        keyboard.append(_nav_row(back_to, with_menu))
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


EXPIRED_HOME = ExpiredCallback(step="home").pack()


def _expired_actions() -> list[InlineKeyboardButton]:
    """Две кнопки истёкшей подписки, ведущие в короткий сценарий.

    Именно в короткий, а не в общий: у истёкшей подписки нет остатка дней, и
    подтверждения с выбором «бесплатно или с доплатой» человеку тут не нужны.
    """
    return [
        ButtonsStorage.RENEW.get_button(
            callback_data=ExpiredCallback(step="renew").pack()
        ),
        ButtonsStorage.CHANGE_PLAN.get_button(
            callback_data=ExpiredCallback(step="plans").pack()
        ),
    ]


def ended() -> InlineKeyboardMarkup:
    """Под сообщением о том, что подписка кончилась.

    Без «Назад» намеренно: это не экран, на который человек пришёл, а
    сообщение, которое пришло к нему само. Возвращать его некуда — позади
    ничего нет, а кнопка предлагала бы уйти вместо того, чтобы продлить.
    """
    return _rows(*_expired_actions())


def expired() -> InlineKeyboardMarkup:
    """То же самое, но там, куда человек пришёл сам, — с возвратом в меню."""
    return _rows(*_expired_actions(), back_to=MENU)


def expired_renew(options) -> InlineKeyboardMarkup:
    """Сроки продления. «Назад» ведёт к сообщению об окончании, а не в меню."""
    # Только «Назад», без «В меню»: сценарий короткий и линейный, лишний
    # выход из него по дороге к оплате ни к чему.
    return _rows(*_renew_buttons(options), back_to=EXPIRED_HOME, with_menu=False)


def expired_renew_final(options) -> InlineKeyboardMarkup:
    """То же, но без «Назад»: сюда человек попадает уже выбрав тариф.

    Возвращать его к экрану «тариф сменён» незачем — там нет ничего, кроме
    предложения оплатить, а он именно это и делает.
    """
    return _rows(*_renew_buttons(options))


def expired_plans(options) -> InlineKeyboardMarkup:
    """Тарифы. Нажатие применяет выбор сразу, без экрана подтверждения."""
    items = [
        InlineKeyboardButton(
            text=f"{option.name} — {option.price_month}₽/мес",
            callback_data=ExpiredCallback(step="pick", device_limit=option.device_limit).pack(),
        )
        for option in options
        if not option.is_current
    ]
    return _rows(*items, back_to=EXPIRED_HOME, with_menu=False)


def device_over_limit_notice() -> InlineKeyboardMarkup:
    """Под разовым сообщением о превышении лимита: удалить лишнее самому или
    поднять тариф под уже подключённые устройства. Без «Назад» — это
    сообщение, которое пришло само, а не экран, куда пришёл человек."""
    return _rows(ButtonsStorage.MY_DEVICES, ButtonsStorage.CHANGE_PLAN)


def trim_warning() -> InlineKeyboardMarkup:
    """Выбор способа: снести самые давние автоматически или отобрать вручную."""
    return _rows(
        InlineKeyboardButton(
            text="Удалить автоматически 🧹",
            callback_data=TrimCallback(action="auto").pack(),
        ),
        InlineKeyboardButton(
            text="Выбрать самому 👆",
            callback_data=TrimCallback(action="manual").pack(),
        ),
        back_to=SUBSCRIPTION,
        with_menu=False,
    )


def trim_pick(devices, selected: set[str]) -> InlineKeyboardMarkup:
    """Список устройств: отмеченные красные.

    Красный — не украшение: у Telegram есть стиль кнопки, и им видно, что
    выбрано, без чтения текста над списком.
    """
    items = [
        InlineKeyboardButton(
            text=("✖ " if device.hwid in selected else "") + device.title,
            callback_data=TrimCallback(action="toggle", token=device.token).pack(),
            style="danger" if device.hwid in selected else None,
        )
        for device in devices
    ]
    items.append(
        InlineKeyboardButton(
            text="Применить", callback_data=TrimCallback(action="apply").pack(), style="success"
        )
    )
    return _rows(*items, back_to=TrimCallback(action="warn").pack(), with_menu=False)


def expired_changed() -> InlineKeyboardMarkup:
    """После смены тарифа остаётся ровно одно действие — заплатить."""
    return _rows(
        ButtonsStorage.RENEW.get_button(
            callback_data=ExpiredCallback(step="renew_final").pack()
        )
    )


def subscription(*, is_active: bool, web_url: str | None, can_add_device: bool) -> InlineKeyboardMarkup:
    """«Подключиться» и «Мои устройства» у истёкшей подписки не показываем:
    кнопка, которая гарантированно откажет, хуже её отсутствия. А вот сменить
    тариф в этот момент как раз естественно — человек решает, за что платить."""
    items: list = []
    if not is_active:
        # Истёкшая подписка ведёт в короткий сценарий: без пересчёта остатка
        # и подтверждений, которым тут неоткуда взяться.
        items.extend(_expired_actions())
        if web_url:
            items.append(InlineKeyboardButton(text=ButtonsStorage.WEB_VERSION.text, url=web_url))
        return _rows(*items, back_to=MENU)

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
        # Через get_button, а не руками: иначе объявленный у кнопки стиль
        # (зелёная) до Telegram не доедет.
        ButtonsStorage.ADD_SUBSCRIPTION.get_button(url=connect_url),
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


def reminder(*, with_devices: bool = False) -> InlineKeyboardMarkup:
    """Напоминание приходит отдельным сообщением, поэтому «Назад» тут нет —
    возвращаться некуда, есть только действие.

    `with_devices` — когда на носу переход на меньший тариф и устройств больше,
    чем он позволяет: человеку нужен прямой путь туда, где он может выбрать
    сам, а не узнать постфактум, что лишние отключились.
    """
    items = [ButtonsStorage.RENEW]
    if with_devices:
        items.append(ButtonsStorage.MY_DEVICES)
    items.append(ButtonsStorage.MY_SUBSCRIPTION)
    return _rows(*items)


def _renew_buttons(options) -> list[InlineKeyboardButton]:
    """Сроки со скидкой. Выгода прямо в кнопке — иначе её никто не заметит."""
    items = []
    for option in options:
        label = f"{option.months} мес. — {option.price}₽"
        if option.saving:
            label += f"  (−{option.saving}₽)"
        items.append(
            InlineKeyboardButton(text=label, callback_data=RenewCallback(months=option.months).pack())
        )
    return items


def renew(options) -> InlineKeyboardMarkup:
    return _rows(*_renew_buttons(options), back_to=SUBSCRIPTION)


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
    """Сообщение с картинкой про выбор туннеля: одна кнопка «Хорошо».

    Не «Мои устройства» и не «Назад»: человек только что подключился и ещё не
    сделал того, о чём просит картинка. Любая кнопка навигации здесь уводит его
    с инструкции раньше, чем он её выполнил. «Хорошо» снимает клавиатуру и
    присылает меню отдельным сообщением — картинка с подписью остаётся в чате,
    к ней можно вернуться.
    """
    return _rows(ButtonsStorage.CONNECT_OK)


def faq_section() -> InlineKeyboardMarkup:
    return only_back(FAQ)
