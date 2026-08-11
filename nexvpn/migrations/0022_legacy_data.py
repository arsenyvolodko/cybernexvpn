"""Шаг 2/3 миграции на VLESS: заводим тарифы и переносим людей из старой базы.

Идёт между 0021 (новые таблицы созданы) и 0023 (таблицы WireGuard удалены) —
единственный момент, когда доступны и новая, и старая схема сразу.

Идемпотентна: пользователи, у которых уже есть `Subscription`, пропускаются.
Дни считаются тем же кодом (`nexvpn.subscription.legacy`), что и в команде
`legacy_report --dry-run`, — то есть отчёт, который смотрят глазами перед
деплоем, показывает ровно то, что здесь и произойдёт.

В панель ничего не пишет: сеть в миграции — плохая идея. Все подписки
создаются со статусом PENDING, их разошлёт `sync_panel`.
"""

from datetime import date, datetime, timedelta, timezone

from django.conf import settings
from django.db import migrations


def seed_plans(apps, schema_editor):
    from nexvpn.subscription.plans import DEFAULT_PLANS

    Plan = apps.get_model("nexvpn", "Plan")
    for device_limit, price_month, name, order in DEFAULT_PLANS:
        Plan.objects.get_or_create(
            device_limit=device_limit,
            defaults={"price_month": price_month, "name": name, "order": order},
        )


def migrate_legacy_users(apps, schema_editor):
    from nexvpn.subscription import legacy
    from nexvpn.subscription.service import normalize_expiry

    NexUser = apps.get_model("nexvpn", "NexUser")
    Plan = apps.get_model("nexvpn", "Plan")
    Subscription = apps.get_model("nexvpn", "Subscription")
    SubscriptionEvent = apps.get_model("nexvpn", "SubscriptionEvent")
    LegacyMigrationRecord = apps.get_model("nexvpn", "LegacyMigrationRecord")

    plans = {plan.device_limit: plan for plan in Plan.objects.all()}
    plan_prices = {limit: plan.price_month for limit, plan in plans.items()}
    if not plan_prices:
        return

    cutoff = date.fromisoformat(settings.LEGACY_CUTOFF_DATE)
    today = datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc)

    already_migrated = set(Subscription.objects.values_list("user_id", flat=True))
    conversions = legacy.convert_all(schema_editor.connection, plan_prices, cutoff, today)

    for conversion in conversions:
        if conversion.user_id in already_migrated:
            continue

        plan = plans[conversion.plan_device_limit]
        subscription = Subscription.objects.create(
            user_id=conversion.user_id,
            plan=plan,
            # Округляем до того же часа, что и обычные начисления: иначе у всех
            # перенесённых подписка кончалась бы в момент деплоя, и напоминания
            # прилетали бы ночью, если выкатывались ночью.
            expires_at=normalize_expiry(now + timedelta(days=conversion.days_granted)),
            panel_status="pending",
        )

        reason = "legacy_no_devices" if conversion.device_count == 0 else "legacy_migration"
        SubscriptionEvent.objects.create(
            user_id=conversion.user_id,
            subscription=subscription,
            reason=reason,
            delta_days=conversion.days_granted,
            plan=plan,
            price_month=plan.price_month,
            expires_at_before=None,
            expires_at_after=subscription.expires_at,
            comment="; ".join(
                [
                    f"баланс {conversion.balance}₽ → {conversion.days_from_balance} дн.",
                    f"остаток периода {conversion.remaining_paid_days} дн.",
                    f"устройств {conversion.device_count}",
                    *conversion.notes,
                ]
            )[:255],
        )

        LegacyMigrationRecord.objects.create(
            user_id=conversion.user_id,
            balance=conversion.balance,
            device_count=conversion.device_count,
            devices_trimmed=conversion.devices_trimmed,
            remaining_paid_days=conversion.remaining_paid_days,
            days_from_balance=conversion.days_from_balance,
            days_granted=conversion.days_granted,
            capped=conversion.capped,
            floored=conversion.floored,
            unlimited=conversion.unlimited,
            plan_device_limit=conversion.plan_device_limit,
            cutoff_date=cutoff,
        )

    # Все, кто есть в базе на этот момент, пришли из старой версии: пробный
    # период им не положен, дни у них уже начислены выше.
    NexUser.objects.update(is_legacy=True)


def noop(apps, schema_editor):
    """Данные не откатываем: разворачивать надо из дампа, а не миграцией."""


class Migration(migrations.Migration):

    dependencies = [
        ("nexvpn", "0021_vless_billing"),
    ]

    operations = [
        migrations.RunPython(seed_plans, noop),
        migrations.RunPython(migrate_legacy_users, noop),
    ]
