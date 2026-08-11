"""Перенос пользователей из старой (WireGuard) версии в новую модель подписки.

Читает легаси-таблицы **сырым SQL**, а не через ORM. Причина: модели `Client`,
`Server`, `UserBalance` из кода уже удалены, но данные в БД ещё живы — до
миграции 0023. Сырой SQL позволяет одному и тому же коду работать и из
дата-миграции, и из команды `legacy_report`, запущенной на восстановленном
дампе для проверки глазами.

Правила конвертации (согласованы 09.08.2026):

- устройство считается активным на дату среза, если его `end_date >= cutoff`,
  то есть оплаченный период ещё не кончился к 3 августа;
- активных устройств на дату среза → тариф: 1→1, 2-3→3, 4-5→5, 6-7→7, 8-10→10,
  больше 10 обрезается до 10;
- ноль устройств → тариф на 3 устройства и 30 дней в подарок;
- баланс → дни по цене нового тарифа, округление **вверх** (в пользу человека);
- сверху добавляется остаток уже оплаченного периода по активным устройствам;
- сумма зажата в `[LEGACY_MIN_DAYS, LEGACY_MAX_DAYS]`;
- баланс выше `LEGACY_UNLIMITED_BALANCE` = аккаунт, которому доступ дарили руками:
  вместо конвертации максимальный тариф и условно бесконечный срок.

Про нижний порог. С 3 августа бот фактически не работал — пополниться было
негде, поэтому у части людей период просто дотикал до нуля не по их вине. Без
порога такой человек получил бы 0 дней, а тот, кто отвалился полгода назад, —
30 подарочных. Порог убирает эту инверсию.
"""

import math
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings

@dataclass
class LegacyUser:
    """Что было у пользователя в старой базе на дату среза."""

    user_id: int
    username: str | None
    balance: int
    device_count: int = 0
    max_end_date: date | None = None
    # Сколько из засчитанных устройств появилось уже после среза. На решение не
    # влияет (правило — по end_date), но полезно видеть в отчёте.
    created_after_cutoff: int = 0


@dataclass
class Conversion:
    """Во что это превращается."""

    user_id: int
    username: str | None
    balance: int
    device_count: int
    devices_trimmed: int
    created_after_cutoff: int
    plan_device_limit: int
    price_month: int
    days_from_balance: int
    remaining_paid_days: int
    bonus_days: int
    days_total: int
    days_granted: int
    capped: bool
    floored: bool
    unlimited: bool
    notes: list[str] = field(default_factory=list)


def read_legacy_users(connection, cutoff: date) -> list[LegacyUser]:
    """Снять состояние старой базы на `cutoff`.

    Активным на срез считается устройство с `end_date >= cutoff`: у остановленных
    подписок `end_date` — это дата, на которой они встали, так что отключённые до
    3 августа отсеиваются сами, а отключённые после — засчитываются.

    Удалённые устройства не восстановить: `Client.delete()` сносил строку. Но
    удалялись только те, что пролежали неактивными два месяца, — на срез они
    всё равно не попадали.
    """
    users: dict[int, LegacyUser] = {}

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT u.id, u.username, COALESCE(b.value, 0)
            FROM nexvpn_nexuser u
            LEFT JOIN nexvpn_userbalance b ON b.user_id = u.id
            """
        )
        for user_id, username, balance in cursor.fetchall():
            users[user_id] = LegacyUser(user_id=user_id, username=username, balance=int(balance or 0))

        cursor.execute(
            """
            SELECT user_id, created_at::date, end_date
            FROM nexvpn_client
            WHERE end_date >= %s
            """,
            [cutoff],
        )
        for user_id, created_at, end_date in cursor.fetchall():
            user = users.get(user_id)
            if user is None:
                continue
            user.device_count += 1
            if created_at > cutoff:
                user.created_after_cutoff += 1
            if end_date and (user.max_end_date is None or end_date > user.max_end_date):
                user.max_end_date = end_date

    return list(users.values())


def convert(legacy: LegacyUser, plan_prices: dict[int, int], today: date) -> Conversion:
    """Посчитать тариф и количество дней для одного пользователя."""
    limits = sorted(plan_prices)
    max_devices = min(settings.LEGACY_MAX_DEVICES, limits[-1])
    notes: list[str] = []

    if legacy.balance > settings.LEGACY_UNLIMITED_BALANCE:
        return _unlimited(legacy, plan_prices, today)

    devices_trimmed = max(0, legacy.device_count - max_devices)
    if devices_trimmed:
        notes.append(f"устройств было {legacy.device_count}, срезано {devices_trimmed}")

    effective_devices = min(legacy.device_count, max_devices)
    bonus_days = 0

    if effective_devices == 0:
        plan_limit = settings.LEGACY_NO_DEVICES_PLAN_DEVICES
        bonus_days = settings.LEGACY_NO_DEVICES_DAYS
        notes.append(f"нет активных устройств → {bonus_days} дн. в подарок")
    else:
        plan_limit = next((limit for limit in limits if effective_devices <= limit), limits[-1])

    if plan_limit not in plan_prices:
        raise ValueError(f"В базе нет тарифа на {plan_limit} устройств")
    price_month = plan_prices[plan_limit]

    days_from_balance = (
        math.ceil(legacy.balance * settings.DAYS_IN_PERIOD / price_month) if legacy.balance > 0 else 0
    )

    remaining_paid_days = 0
    if legacy.max_end_date and legacy.max_end_date > today:
        remaining_paid_days = (legacy.max_end_date - today).days

    days_total = bonus_days + days_from_balance + remaining_paid_days
    days_granted = max(min(days_total, settings.LEGACY_MAX_DAYS), settings.LEGACY_MIN_DAYS)
    capped = days_granted < days_total
    floored = days_granted > days_total
    if capped:
        notes.append(f"потолок: {days_total} → {days_granted} дн.")
    if floored:
        notes.append(f"нижний порог: {days_total} → {days_granted} дн.")

    return Conversion(
        user_id=legacy.user_id,
        username=legacy.username,
        balance=legacy.balance,
        device_count=legacy.device_count,
        devices_trimmed=devices_trimmed,
        created_after_cutoff=legacy.created_after_cutoff,
        plan_device_limit=plan_limit,
        price_month=price_month,
        days_from_balance=days_from_balance,
        remaining_paid_days=remaining_paid_days,
        bonus_days=bonus_days,
        days_total=days_total,
        days_granted=days_granted,
        capped=capped,
        floored=floored,
        unlimited=False,
        notes=notes,
    )


def _unlimited(legacy: LegacyUser, plan_prices: dict[int, int], today: date) -> Conversion:
    """Аккаунты с балансом, залитым руками мимо транзакций.

    Это свои и друзья, которым доступ выдавали навсегда. Конвертировать такой
    баланс в дни бессмысленно — 100 000₽ превращаются в двадцать лет подписки,
    а обрезать потолком значит отобрать то, что было подарено. Поэтому им сразу
    максимальный тариф и условно бесконечный срок.
    """
    plan_limit = settings.LEGACY_UNLIMITED_PLAN_DEVICES
    if plan_limit not in plan_prices:
        plan_limit = max(plan_prices)
    days = settings.LEGACY_UNLIMITED_DAYS

    remaining_paid_days = 0
    if legacy.max_end_date and legacy.max_end_date > today:
        remaining_paid_days = (legacy.max_end_date - today).days

    return Conversion(
        user_id=legacy.user_id,
        username=legacy.username,
        balance=legacy.balance,
        device_count=legacy.device_count,
        devices_trimmed=0,
        created_after_cutoff=legacy.created_after_cutoff,
        plan_device_limit=plan_limit,
        price_month=plan_prices[plan_limit],
        days_from_balance=0,
        remaining_paid_days=remaining_paid_days,
        bonus_days=days,
        days_total=days,
        days_granted=days,
        capped=False,
        floored=False,
        unlimited=True,
        notes=[f"баланс залит вручную ({legacy.balance}₽) → безлимит, {plan_limit} устр."],
    )


def convert_all(connection, plan_prices: dict[int, int], cutoff: date, today: date) -> list[Conversion]:
    return [convert(legacy, plan_prices, today) for legacy in read_legacy_users(connection, cutoff)]
