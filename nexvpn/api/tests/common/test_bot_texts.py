"""Разметка текстов бота.

Появилось после живого бага: в тексте стояло `<>` вместо `<b>`. Telegram
отказался разбирать сущности, правка экрана не прошла, сообщение удалилось, а
повторная отправка упала на той же разметке — человек остался с пустотой вместо
меню. Проверка дешёвая, а ловит целый класс опечаток.
"""

import re
from html.parser import HTMLParser

import pytest

# Питоновский парсер считает `<>` обычным текстом и молча пропускает — Telegram
# нет. Поэтому отдельно требуем, чтобы за каждым `<` шла буква или косая черта.
BARE_BRACKET = re.compile(r"<(?![a-zA-Z/])")

from bot import texts

# Что Telegram понимает в parse_mode=HTML.
ALLOWED = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "a", "code", "pre", "span", "tg-spoiler", "tg-emoji", "blockquote", "br",
}
VOID = {"br"}


class Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.problems.append(f"неизвестный тег <{tag}>")
        elif tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(f"закрывается </{tag}>, а открыт {self.stack[-1:] or 'ничего'}")
        else:
            self.stack.pop()

    def finish(self) -> list[str]:
        if self.stack:
            self.problems.append(f"не закрыты: {self.stack}")
        return self.problems


def problems_in(text: str) -> list[str]:
    checker = Checker()
    checker.feed(text)
    checker.close()
    problems = list(checker.finish())
    problems += [f"похоже на обрывок тега: {text[m.start():m.start() + 12]!r}"
                 for m in BARE_BRACKET.finditer(text)]
    return problems


def message_texts():
    return {
        name: value
        for name, value in vars(texts).items()
        if not name.startswith("_") and isinstance(value, str) and value
    }


@pytest.mark.parametrize("name", sorted(message_texts()))
def test_text_is_valid_telegram_html(name):
    problems = problems_in(message_texts()[name])

    assert not problems, f"{name}: {problems}"


@pytest.mark.parametrize(
    "broken",
    [
        "Текущий тариф: <>3 устройства",  # та самая опечатка
        "Осталось <b>3 дня",  # не закрыт
        "Тариф <marquee>лучший</marquee>",  # тега нет в Telegram
        "Цена < 100 рублей",  # знак «меньше» ломает разбор
    ],
)
def test_broken_markup_is_caught(broken):
    assert problems_in(broken)
