"""Выдача ссылки подписки на другом хосте."""

import pytest

from nexvpn.models import Subscription


def make(url, override=""):
    s = Subscription(subscription_url=url, sub_host_override=override)
    return s


def test_without_override_link_is_untouched():
    s = make("https://sub-nex.com/ShFz9bu699hZmcYH")

    assert s.public_subscription_url == "https://sub-nex.com/ShFz9bu699hZmcYH"


def test_override_changes_only_the_host():
    """Короткий идентификатор один и тот же на всех зеркалах — меняем имя."""
    s = make("https://sub-nex.com/ShFz9bu699hZmcYH", "sub.cybernexapp.com")

    assert s.public_subscription_url == "https://sub.cybernexapp.com/ShFz9bu699hZmcYH"


def test_override_survives_extra_path_segments():
    s = make("https://sub-nex.com/api/sub/ABC", "sub.cybernexapp.com")

    assert s.public_subscription_url == "https://sub.cybernexapp.com/api/sub/ABC"


def test_empty_url_stays_empty():
    assert make(None, "sub.cybernexapp.com").public_subscription_url == ""
    assert make("", "sub.cybernexapp.com").public_subscription_url == ""


def test_whitespace_override_is_ignored():
    s = make("https://sub-nex.com/ABC", "   ")

    assert s.public_subscription_url == "https://sub-nex.com/ABC"


def test_panel_sync_does_not_wipe_the_override():
    """Синхронизация переписывает только адрес из панели, наш выбор остаётся.

    Проверяем исходник, а не поведение: единственный способ потерять настройку —
    если кто-то однажды добавит это поле в список переписываемых.
    """
    from nexvpn.subscription import panel_sync

    source = open(panel_sync.__file__).read()

    assert "sub_host_override" not in source
