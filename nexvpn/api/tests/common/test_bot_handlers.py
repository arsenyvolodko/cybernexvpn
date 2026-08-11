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
