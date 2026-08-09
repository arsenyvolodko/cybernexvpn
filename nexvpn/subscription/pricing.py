"""Вся денежная арифметика подписки. Чистые функции на целых числах, без ORM.

Единица периода — 30 дней (settings.DAYS_IN_PERIOD). Цена дня как таковая
нигде не хранится и не округляется: любое деление делается один раз, в самом
конце, чтобы не накапливать погрешность.

Ключевой инвариант при смене тарифа — **сохраняется стоимость остатка, а не
количество дней**. Иначе апгрейд у пользователя с большим остатком (а такие
будут сразу после миграции старой базы) сжигал бы его деньги.
"""

import math
from dataclasses import dataclass

from django.conf import settings


def days_in_period() -> int:
    return settings.DAYS_IN_PERIOD


def days_from_amount(amount: int, price_month: int) -> int:
    """Сколько полных дней тарифа покупает сумма `amount`. Округление вниз."""
    if price_month <= 0:
        raise ValueError("price_month must be positive")
    return amount * days_in_period() // price_month


def days_from_amount_generous(amount: int, price_month: int) -> int:
    """То же, но с округлением вверх — для разовых начислений в пользу пользователя."""
    if price_month <= 0:
        raise ValueError("price_month must be positive")
    return math.ceil(amount * days_in_period() / price_month)


def value_of_days(days: int, price_month: int) -> int:
    """Сколько стоит остаток в `days` дней на тарифе `price_month`. Округление вниз."""
    return days * price_month // days_in_period()


def convert_days_between_plans(days_left: int, price_from: int, price_to: int) -> int:
    """Пересчёт остатка при смене тарифа с сохранением стоимости.

    30 дней по 150₽ при переходе на 400₽ → 11 дней. 300 дней по 150₽ → 112 дней.
    Ни рубля не теряется, что бы ни было с размером остатка.
    """
    if days_left <= 0:
        return 0
    if price_to <= 0:
        raise ValueError("price_to must be positive")
    return days_left * price_from // price_to


def topup_price_for_full_period(days_left: int, price_from: int, price_to: int) -> int:
    """Доплата, чтобы после перехода получить полный период на новом тарифе.

    Это формула из ТЗ: цена_нового − стоимость_остатка_старого, но не меньше нуля.
    """
    return max(0, price_to - value_of_days(days_left, price_from))


@dataclass(frozen=True)
class PlanChangeQuote:
    """Расчёт перехода на другой тариф — то, что бот показывает пользователю."""

    is_upgrade: bool
    days_left: int
    price_from: int
    price_to: int

    # Вариант «перейти прямо сейчас, ничего не доплачивая»
    converted_days: int

    # Вариант «доплатить и получить полный период». None — когда он невыгоден,
    # то есть когда после бесплатного пересчёта дней и так остаётся не меньше периода.
    topup_price: int | None
    topup_days: int | None

    @property
    def can_topup(self) -> bool:
        return self.topup_price is not None


def quote_plan_change(days_left: int, price_from: int, price_to: int) -> PlanChangeQuote:
    converted_days = convert_days_between_plans(days_left, price_from, price_to)

    topup_price: int | None = None
    topup_days: int | None = None
    # Доплату предлагаем, только если она реально что-то добавляет. Иначе получилось бы
    # «заплати 0₽ и получи 30 дней вместо своих 112» — это отъём остатка.
    if converted_days < days_in_period():
        topup_price = topup_price_for_full_period(days_left, price_from, price_to)
        topup_days = days_in_period()

    return PlanChangeQuote(
        is_upgrade=price_to > price_from,
        days_left=days_left,
        price_from=price_from,
        price_to=price_to,
        converted_days=converted_days,
        topup_price=topup_price,
        topup_days=topup_days,
    )


def plan_for_device_count(device_count: int, available_limits: list[int]) -> int:
    """Ближайший тариф, вмещающий `device_count` устройств.

    1 → 1, 2-3 → 3, 4-5 → 5, 6-7 → 7, 8-10 → 10, больше 10 → 10 (лишнее срезаем).
    """
    limits = sorted(available_limits)
    if not limits:
        raise ValueError("no plans configured")
    for limit in limits:
        if device_count <= limit:
            return limit
    return limits[-1]
