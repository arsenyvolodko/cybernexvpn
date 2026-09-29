"""Режим вебхука у бота: путь и выбор режима."""

import pytest
from django.test import override_settings

from bot.main import webhook_path


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://cybernexvpn.ru/tg/abc123/", "/tg/abc123/"),
        ("https://cybernexvpn.ru/tg/abc123", "/tg/abc123/"),
        ("http://example.com/hook", "/hook/"),
        ("https://cybernexvpn.ru", "/"),
        ("https://cybernexvpn.ru/", "/"),
    ],
)
def test_path_is_normalised(url, expected):
    """Расхождение в хвостовом слеше — это 404 на каждый апдейт."""
    assert webhook_path(url) == expected


@override_settings(TG_WEBHOOK_URL="")
def test_without_url_we_stay_on_polling():
    """Пустой адрес — прежнее поведение, опрос. Ничего не ломаем по умолчанию."""
    from django.conf import settings

    assert not settings.TG_WEBHOOK_URL


@override_settings(TG_WEBHOOK_URL="https://cybernexvpn.ru/tg/x/")
def test_with_url_webhook_mode_is_chosen():
    from django.conf import settings

    assert settings.TG_WEBHOOK_URL
    assert webhook_path(settings.TG_WEBHOOK_URL) == "/tg/x/"
