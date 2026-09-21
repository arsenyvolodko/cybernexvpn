"""Проверки самой раскладки бота, а не бизнес-логики.

Появились после живой ошибки: кнопку «Написать в поддержку» обрабатывали два
хендлера в разных роутерах. Побеждал тот, чей роутер подключён раньше, — и
сценарий молча ломался, потому что «неправильный» победитель не выставлял
состояние ожидания. Такое не ловится ни одним тестом на логику.
"""

import re
from collections import defaultdict
from pathlib import Path

import pytest

from bot.handlers import build_router
from bot.handlers.menu import legacy_router
from bot.keyboards import ALL_CALLBACKS, ButtonsStorage

HANDLERS_DIR = Path(__file__).resolve().parents[4] / "bot" / "handlers"
DECORATOR = re.compile(r"F\.data\s*==\s*ButtonsStorage\.(\w+)\.callback")
IN_SET = re.compile(r"F\.data\.in_\(\{([^}]*)\}\)")


def declared_buttons() -> dict[str, list[str]]:
    """Какие кнопки какой файл берётся обрабатывать."""
    owners: dict[str, list[str]] = defaultdict(list)
    for path in HANDLERS_DIR.glob("*.py"):
        source = path.read_text()
        for name in DECORATOR.findall(source):
            owners[name].append(path.name)
        for group in IN_SET.findall(source):
            for name in re.findall(r"ButtonsStorage\.(\w+)\.callback", group):
                owners[name].append(path.name)
    return owners


def test_no_button_is_handled_twice():
    duplicates = {
        name: sorted(set(files)) for name, files in declared_buttons().items() if len(files) > 1
    }
    assert not duplicates, f"Кнопку обрабатывают несколько хендлеров: {duplicates}"


def test_every_menu_button_has_a_handler():
    """Кнопка без обработчика уводит человека в заглушку вместо сценария."""
    handled = set(declared_buttons())
    menu_buttons = {"CONNECT", "MY_SUBSCRIPTION", "REFERRAL", "FAQ_SUPPORT"}
    assert menu_buttons <= handled, f"Без обработчика: {menu_buttons - handled}"


def test_routers_are_in_the_expected_order():
    """legacy-роутер обязан быть последним: он ловит всё нераспознанное."""
    names = [router.name for router in build_router().sub_routers]
    assert names[-1] == "legacy", names


def test_unknown_button_never_dead_ends():
    """Кнопки старого бота остались в чатах у сотен людей навсегда.

    Часть их имён совпадает с нашими (`add_device_callback`), так что делить
    по списку callback'ов нельзя — нужен обработчик вообще без фильтров.
    Иначе человек жмёт кнопку и видит вечный спиннер.
    """
    handlers = legacy_router.callback_query.handlers
    assert any(not handler.filters for handler in handlers), (
        "В legacy-роутере нет обработчика без фильтров"
    )


def test_unknown_message_never_dead_ends():
    """Потерянный из БД пользователь может просто написать текстом.

    Без такого обработчика бот молчит, и непонятно, дошло ли хоть что-то.
    """
    handlers = legacy_router.message.handlers
    assert any(not handler.filters for handler in handlers), (
        "В legacy-роутере нет обработчика сообщений без фильтров"
    )


@pytest.mark.parametrize("name", sorted(ALL_CALLBACKS))
def test_callback_data_fits_telegram_limit(name):
    assert len(name.encode()) <= 64


def test_button_texts_are_unique():
    """Две кнопки с одинаковой подписью на одном экране путают людей."""
    texts = [
        button.txt
        for button in vars(ButtonsStorage).values()
        if hasattr(button, "txt") and button.txt
    ]
    duplicates = {text for text in texts if texts.count(text) > 1}
    assert not duplicates, f"Повторяющиеся подписи: {duplicates}"


def test_connect_button_keeps_its_style():
    """Кнопка-ссылка легко теряет стиль.

    Она собирается не из `Button.get_button()`, а вручную из текста — и тогда
    объявленный у кнопки `style` до Telegram не доезжает, кнопка молча остаётся
    серой. Проверяем не объявление, а то, что реально уходит в клавиатуре.
    """
    from bot.apps_catalog import Platform
    from bot.keyboards import keyboards
    from bot.keyboards.storage import ButtonsStorage

    keyboard = keyboards.platform_connect(
        Platform.IOS, "https://incy.app/add/abc", "https://sub-nex.com/abc"
    )

    buttons = [button for row in keyboard.inline_keyboard for button in row]
    connect = next(b for b in buttons if b.text == ButtonsStorage.ADD_SUBSCRIPTION.text)
    assert connect.style == "success"
    assert connect.url, "это кнопка-ссылка, она должна вести в приложение"


def test_main_menu_buttons_carry_animated_icons():
    """Иконка — отдельное поле кнопки, его легко потерять по дороге в Telegram.
    Проверяем то, что реально уходит в клавиатуре главного меню."""
    from bot.keyboards import keyboards

    buttons = [button for row in keyboards.main_menu().inline_keyboard for button in row]
    icons = {button.text: button.icon_custom_emoji_id for button in buttons}

    assert icons == {
        "Подключиться": "5411590687663608498",
        "Моя подписка": "5278573677900752088",
        "Реферальная программа": "5203996991054432397",
        "FAQ и поддержка": "5443038326535759644",
    }


def test_share_referral_button_carries_its_icon():
    """Кнопка-ссылка раньше собиралась вручную из текста — иконка бы не доехала."""
    from bot.keyboards import keyboards

    share = keyboards.referral("https://t.me/bot?start=1").inline_keyboard[0][0]
    assert share.text == "Поделиться ссылкой"
    assert share.icon_custom_emoji_id == "5190859184312167965"
    assert share.url == "https://t.me/share/url?url=https://t.me/bot?start=1"


def test_copy_referral_button_copies_the_link():
    from bot.keyboards import keyboards

    copy = keyboards.referral("https://t.me/bot?start=1").inline_keyboard[1][0]
    assert copy.text == "Скопировать ссылку"
    assert copy.icon_custom_emoji_id == "5413422358071372326"
    assert copy.copy_text.text == "https://t.me/bot?start=1"


def test_renew_button_carries_its_icon_wherever_it_appears():
    """«Продлить» живёт на нескольких экранах — иконка должна доехать на каждом."""
    from bot.keyboards import keyboards
    from bot.keyboards.storage import ButtonsStorage

    button = ButtonsStorage.RENEW.get_button()
    assert button.text == "Продлить подписку"
    assert button.icon_custom_emoji_id == "5267300544094948794"
    assert button.style == "success"

    # В напоминании кнопка собирается через общий список — проверяем и там.
    reminder = keyboards.reminder()
    renew = next(b for row in reminder.inline_keyboard for b in row if b.text == "Продлить подписку")
    assert renew.icon_custom_emoji_id == "5267300544094948794"


def test_the_screen_text_names_the_button_that_exists():
    """Текст просит «нажми ...» — имя должно совпадать с кнопкой на экране."""
    from bot import texts
    from bot.keyboards.storage import ButtonsStorage

    name = ButtonsStorage.ADD_SUBSCRIPTION.text.split()[0]
    assert f"«{name}»" in texts.CONNECT_ADD_SUBSCRIPTION
