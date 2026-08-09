"""Формирование платежа и чека. Всё настраивается из админки."""

import pytest

from nexvpn import payments
from nexvpn.api.tests.factories import PlanFactory
from nexvpn.models import GlobalSettings

pytestmark = pytest.mark.django_db


@pytest.fixture
def purpose():
    return payments.PaymentPurpose(amount=400, plan=PlanFactory(device_limit=3, price_month=400), days=30)


def test_no_receipt_until_enabled_in_admin(purpose):
    """До подключения кассы чек не формируется и email не нужен."""
    data = payments.build_payment_data(purpose, email=None)

    assert "receipt" not in data
    assert data["amount"] == {"value": "400.00", "currency": "RUB"}


def test_receipt_built_from_admin_settings(purpose):
    billing = GlobalSettings.load()
    billing.receipt_enabled = True
    billing.save()

    data = payments.build_payment_data(purpose, email="a@b.ru", billing=billing)
    item = data["receipt"]["items"][0]

    assert data["receipt"]["customer"]["email"] == "a@b.ru"
    assert item["vat_code"] == 1  # самозанятый: без НДС
    assert item["payment_subject"] == "service"
    assert item["description"] == "Подписка CyberNex: 3 устр., 30 дн."


def test_receipt_requires_email_when_enabled(purpose):
    billing = GlobalSettings.load()
    billing.receipt_enabled = True
    billing.save()

    with pytest.raises(payments.PaymentDataError):
        payments.build_payment_data(purpose, email=None, billing=billing)


def test_wording_is_editable(purpose):
    billing = GlobalSettings.load()
    billing.receipt_enabled = True
    billing.payment_description = "Оплата услуг связи"
    billing.purchase_item_template = "Доступ к сервису, {days} дн. ({devices} устр.)"
    billing.save()

    data = payments.build_payment_data(purpose, email="a@b.ru", billing=billing)

    assert data["description"] == "Оплата услуг связи"
    assert data["receipt"]["items"][0]["description"] == "Доступ к сервису, 30 дн. (3 устр.)"


def test_broken_template_falls_back_instead_of_breaking_payment(purpose):
    """Опечатка в шаблоне из админки не должна ронять оплату."""
    billing = GlobalSettings.load()
    billing.receipt_enabled = True
    billing.purchase_item_template = "Подписка на {nonexistent} дней"
    billing.save()

    data = payments.build_payment_data(purpose, email="a@b.ru", billing=billing)

    assert data["receipt"]["items"][0]["description"] == "Подписка CyberNex: 3 устр., 30 дн."


def test_no_vpn_word_in_client_facing_strings(purpose):
    billing = GlobalSettings.load()
    billing.receipt_enabled = True
    billing.save()

    data = payments.build_payment_data(purpose, email="a@b.ru", billing=billing)

    assert "vpn" not in data["description"].lower()
    assert "vpn" not in data["receipt"]["items"][0]["description"].lower()


def test_maintenance_is_off_by_default():
    settings_obj = GlobalSettings.load()

    assert settings_obj.maintenance_mode is False
    assert settings_obj.maintenance_message
    assert settings_obj.maintenance_until is None


def test_maintenance_does_not_touch_payments(purpose):
    """Заглушка — только для бота: уже начатую оплату она ломать не должна."""
    settings_obj = GlobalSettings.load()
    settings_obj.maintenance_mode = True
    settings_obj.save()

    data = payments.build_payment_data(purpose, email="a@b.ru", billing=settings_obj)

    assert data["amount"] == {"value": "400.00", "currency": "RUB"}


def test_settings_are_singleton():
    first = GlobalSettings.load()
    first.receipt_enabled = True
    first.save()

    second = GlobalSettings(receipt_enabled=False)
    second.save()

    assert GlobalSettings.objects.count() == 1
    assert second.pk == first.pk
