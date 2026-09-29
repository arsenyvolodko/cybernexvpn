"""Домен задаётся в одном месте и попадает во все нужные списки."""

from django.conf import settings


def test_domain_is_in_allowed_hosts():
    assert settings.DOMAIN_NAME in settings.ALLOWED_HOSTS
    assert f"www.{settings.DOMAIN_NAME}" in settings.ALLOWED_HOSTS


def test_domain_is_trusted_for_csrf():
    """Без этого админка на новом домене отдаёт 403 при любой отправке формы."""
    assert f"https://{settings.DOMAIN_NAME}" in settings.CSRF_TRUSTED_ORIGINS


def test_old_domain_is_gone_from_code():
    """От старого имени отказались: в настройках его быть не должно."""
    assert not any("cybernexvpn.ru" in host for host in settings.ALLOWED_HOSTS)
    assert not any("cybernexvpn.ru" in o for o in settings.CSRF_TRUSTED_ORIGINS)
