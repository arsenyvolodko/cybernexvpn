"""Платежи YooKassa.

Два отличия от старой версии:

1. Платим не «за пополнение баланса», а за конкретный период подписки —
   баланса как сущности больше нет.
2. Формируется чек по 54-ФЗ. Раньше email клали в `metadata`, что чеком не
   является: ФНС такой платёж не видит.

Всё, что относится к чеку — включать ли его, ставка НДС, формулировки, —
лежит в `GlobalSettings` и правится в админке. Так чек можно включить в тот
день, когда подключится касса, не выкатывая релиз.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from nexvpn.enums import PaymentKindEnum
from nexvpn.models import GlobalSettings, NexUser, Payment, Plan

logger = logging.getLogger(__name__)

FALLBACK_DESCRIPTION = "Оплата подписки CyberNex"
FALLBACK_ITEM = "Подписка CyberNex: {devices} устр., {days} дн."


class PaymentDataError(Exception):
    """Не хватает данных, чтобы создать платёж."""


@dataclass(frozen=True)
class PaymentPurpose:
    """Что именно покупается — попадает в позицию чека."""

    amount: int
    plan: Plan
    days: int
    is_plan_change: bool = False


def _render(template: str, fallback: str, **context) -> str:
    """Подставить значения в шаблон из админки, не падая на опечатке в нём."""
    try:
        return template.format(**context)
    except (KeyError, IndexError, ValueError):
        logger.warning("Некорректный шаблон в настройках биллинга: %r", template)
        return fallback.format(**context)


def build_payment_data(
    purpose: PaymentPurpose,
    email: str | None,
    return_url: str | None = None,
    billing: GlobalSettings | None = None,
) -> dict[str, Any]:
    billing = billing or GlobalSettings.load()
    context = {"devices": purpose.plan.device_limit, "days": purpose.days}

    amount = {"value": f"{purpose.amount}.00", "currency": "RUB"}
    payment_data: dict[str, Any] = {
        "amount": amount,
        "confirmation": {
            "type": "redirect",
            "return_url": return_url or settings.TG_BOT_URL,
        },
        "capture": True,
        "description": _render(billing.payment_description, FALLBACK_DESCRIPTION, **context)[:128],
        "metadata": {
            "plan_device_limit": str(purpose.plan.device_limit),
            "days": str(purpose.days),
            "is_plan_change": "1" if purpose.is_plan_change else "0",
        },
    }

    if not billing.receipt_enabled:
        return payment_data

    if not email:
        raise PaymentDataError("Для чека нужен email — спросите его до создания платежа")

    template = billing.plan_change_item_template if purpose.is_plan_change else billing.purchase_item_template
    payment_data["receipt"] = {
        "customer": {"email": email},
        "items": [
            {
                "description": _render(template, FALLBACK_ITEM, **context)[:128],
                "quantity": "1.00",
                "amount": amount,
                "vat_code": billing.vat_code,
                "payment_subject": billing.payment_subject,
                "payment_mode": billing.payment_mode,
            }
        ],
    }
    return payment_data


def new_idempotence_key() -> uuid.UUID:
    return uuid.uuid4()


@dataclass(frozen=True)
class CreatedPayment:
    url: str
    amount: int
    payment: "Payment"


def create_payment(
    user: "NexUser",
    plan: Plan,
    *,
    amount: int,
    days: int,
    months: int = 1,
    kind: str = PaymentKindEnum.PURCHASE,
    return_url: str | None = None,
) -> CreatedPayment:
    """Создать платёж в YooKassa и записать намерение.

    Одна функция на бота и на API: сумма считается снаружи, но всё, что
    касается «за что заплачено», пишется здесь — иначе вебхук не сможет понять,
    что начислять, и два пути начали бы расходиться.
    """
    import yookassa
    from django.db import transaction

    from nexvpn.enums import TransactionStatusEnum, TransactionTypeEnum
    from nexvpn.models import Payment, Transaction

    billing = GlobalSettings.load()
    if billing.receipt_enabled and not user.email:
        raise PaymentDataError("Для чека нужен email")

    purpose = PaymentPurpose(
        amount=amount, plan=plan, days=days,
        is_plan_change=(kind == PaymentKindEnum.PLAN_CHANGE),
    )
    payment_data = build_payment_data(purpose, email=user.email, return_url=return_url, billing=billing)

    with transaction.atomic():
        idempotence_key = new_idempotence_key()
        created = yookassa.Payment.create(payment_data, idempotence_key)
        if created.status != "pending":
            raise PaymentDataError(f"Неожиданный статус платежа: {created.status}")

        payment = Payment.objects.create(
            uuid=created.id,
            idempotence_key=idempotence_key,
            user=user,
            kind=kind,
            plan=plan,
            amount=amount,
            period_months=months,
        )
        Transaction.objects.create(
            user=user,
            is_credit=True,
            value=amount,
            payment=payment,
            type=(
                TransactionTypeEnum.PLAN_UPGRADE
                if kind == PaymentKindEnum.PLAN_CHANGE
                else TransactionTypeEnum.PURCHASE_SUBSCRIPTION
            ),
            status=TransactionStatusEnum.WAITING_FOR_CAPTURE,
        )

    return CreatedPayment(url=created.confirmation.confirmation_url, amount=amount, payment=payment)
