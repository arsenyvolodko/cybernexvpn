"""Проверка источника вебхука YooKassa."""

import pytest
from django.test import RequestFactory, override_settings

from nexvpn.webhook_ips import get_client_ip, is_yookassa_ip, verify_webhook_source

YOOKASSA_IP = "185.71.76.5"  # внутри 185.71.76.0/27
YOOKASSA_EDGE = "77.75.154.129"  # внутри 77.75.154.128/25 — раньше маска терялась
YOOKASSA_IPV6 = "2a02:5180:0:1::7"
STRANGER = "203.0.113.7"


def request_from(*, remote_addr=STRANGER, forwarded=None):
    headers = {"REMOTE_ADDR": remote_addr}
    if forwarded is not None:
        headers["HTTP_X_FORWARDED_FOR"] = forwarded
    return RequestFactory().post("/api/v1/payment_succeeded/", **headers)


@pytest.mark.parametrize("raw", [YOOKASSA_IP, YOOKASSA_EDGE, YOOKASSA_IPV6, "77.75.156.11"])
def test_known_addresses_are_recognised(raw):
    assert is_yookassa_ip(get_client_ip(request_from(remote_addr=raw)))


@pytest.mark.parametrize("raw", [STRANGER, "185.71.76.64", "77.75.156.12"])
def test_foreign_addresses_are_not(raw):
    """185.71.76.64 вне /27, 77.75.156.12 — соседний с разрешённым одиночным."""
    assert not is_yookassa_ip(get_client_ip(request_from(remote_addr=raw)))


def test_takes_the_address_added_by_our_proxy_not_the_client_one():
    """Главное свойство: подделать заголовок нельзя.

    Nginx дописывает настоящий адрес в конец, поэтому левую часть, которую
    прислал клиент, во внимание брать нельзя — иначе кто угодно представится
    YooKassa одним заголовком.
    """
    request = request_from(forwarded=f"{YOOKASSA_IP}, {STRANGER}")

    assert str(get_client_ip(request)) == STRANGER
    assert not is_yookassa_ip(get_client_ip(request))


def test_real_notification_through_proxy_passes():
    request = request_from(forwarded=f"{STRANGER}, {YOOKASSA_IP}")
    assert str(get_client_ip(request)) == YOOKASSA_IP


def test_falls_back_to_remote_addr_without_proxy_header():
    assert str(get_client_ip(request_from(remote_addr=YOOKASSA_IP))) == YOOKASSA_IP


def test_broken_header_does_not_crash():
    assert get_client_ip(request_from(forwarded="не-адрес")) is None


@override_settings(YOOKASSA_IP_CHECK_ENFORCE=False)
def test_soft_mode_lets_everything_through():
    """Режим по умолчанию: только лог, платежи не рвутся из-за ошибки в настройке."""
    allowed, _ = verify_webhook_source(request_from(remote_addr=STRANGER))
    assert allowed


@override_settings(YOOKASSA_IP_CHECK_ENFORCE=True)
def test_strict_mode_rejects_strangers():
    allowed, _ = verify_webhook_source(request_from(remote_addr=STRANGER))
    assert not allowed


@override_settings(YOOKASSA_IP_CHECK_ENFORCE=True)
def test_strict_mode_accepts_yookassa():
    allowed, _ = verify_webhook_source(request_from(remote_addr=YOOKASSA_IP))
    assert allowed
