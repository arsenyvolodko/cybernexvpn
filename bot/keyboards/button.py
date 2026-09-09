"""Кнопка с автогенерацией callback-имени.

Паттерн из старого бота: имя атрибута в `ButtonsStorage` становится и
callback_data, и ключом для текста. Так нельзя разъехаться между объявлением
кнопки, её текстом и обработчиком.
"""

from aiogram.types import InlineKeyboardButton


class Button:
    """Кнопка меню.

    `style` — это поле Bot API: "success" (зелёная), "danger" (красная),
    "primary" (синяя). Без него клиент рисует кнопку своим обычным цветом.
    Старые версии Telegram поле просто игнорируют, так что подстраховка не
    нужна: кнопка останется обычной, но рабочей.
    """

    def __init__(self, text: str | None = None, style: str | None = None) -> None:
        self.name: str | None = None
        self.txt: str | None = text
        self.style: str | None = style
        self.callback_suffix: str = "_callback"

    def __str__(self) -> str:
        return self.callback

    @property
    def text(self) -> str:
        return self.txt

    @property
    def callback(self) -> str:
        return self.name.lower() + self.callback_suffix

    def get_button(self, **kwargs) -> InlineKeyboardButton:
        """Кнопка. `callback_data` можно переопределить.

        Переопределение нужно там, где действие то же, а ведёт оно в другой
        сценарий: «Продлить подписку» у истёкшей подписки идёт в короткий
        путь, а не в общий. Раньше этот аргумент молча игнорировался, и кнопка
        выглядела правильной, но вела не туда.
        """
        text = kwargs.get("text", self.txt)
        style = kwargs.get("style", self.style)
        url = kwargs.get("url")
        if url:
            return InlineKeyboardButton(text=text, url=url, style=style)
        callback_data = kwargs.get("callback_data", self.callback)
        return InlineKeyboardButton(text=text, callback_data=callback_data, style=style)


class AutoNameButtonMeta(type):
    """Проставляет кнопкам имя атрибута и текст из соседнего хранилища текстов."""

    text_storage: type | None = None

    def __new__(mcs, name, bases, namespace):
        storage = namespace.get("_texts")
        for attr_name, value in namespace.items():
            if isinstance(value, Button):
                if not value.name:
                    value.name = attr_name
                if not value.txt and storage is not None:
                    value.txt = getattr(storage, attr_name)
        return type.__new__(mcs, name, bases, namespace)
